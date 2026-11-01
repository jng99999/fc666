"""Private operator credentials and encrypted, revisioned runtime configuration.

The auth database never contains API credentials. The independently mounted
vault contains only authenticated ciphertext, its revision, and a key kept on
the server. Workers may open that directory read-only, without auth sessions.
"""
from contextlib import contextmanager
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from typing import Callable
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken


COOKIE = 'fc666_admin_session'
SESSION_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')
HASH_SLOTS = threading.BoundedSemaphore(1)
SECRET_FIELDS = {'model': 'api_key', 'news': 'token', 'gold': 'token'}


class OperatorError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def validate_public_url(value: str, resolver=None) -> str:
    """Validate all DNS answers; callers must also pin/revalidate at request time.

    No query or fragment is accepted, so an operator cannot accidentally place
    a provider credential into a URL that might be logged by infrastructure.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or value != value.strip():
        raise OperatorError(422, '接口地址必须是有效的公网 HTTPS 地址')
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = 443 if parsed.port is None else parsed.port
        name = hostname.rstrip('.').lower() if hostname else ''
        if (parsed.scheme != 'https' or not hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or '\\' in value or any(ord(char) <= 32 for char in value)
                or not 1 <= port <= 65535 or '%' in hostname or name == 'localhost'
                or name.endswith(('.localhost', '.local', '.internal'))):
            raise ValueError('URL boundary')
        answers = (resolver or socket.getaddrinfo)(hostname, port, type=socket.SOCK_STREAM)
        addresses = {ipaddress.ip_address(answer[4][0].split('%')[0]) for answer in answers}
        if not addresses or any(not address.is_global or address.is_multicast
                                or (getattr(address, 'ipv4_mapped', None) is not None
                                    and not address.ipv4_mapped.is_global)
                                for address in addresses):
            raise ValueError('Nonpublic DNS answer')
    except (ValueError, UnicodeError, OSError, TypeError, IndexError):
        raise OperatorError(422, '接口地址必须解析到公网，禁止内网、查询参数和重定向地址') from None
    return value.rstrip('/')


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _empty_config() -> dict:
    return {'model': {'provider': '', 'model': '', 'base_url': '', 'api_key': ''},
            'news': {'source': '', 'url': '', 'token': ''},
            'gold': {'source': '', 'url': '', 'token': ''}, 'authorized_accounts': []}


def _bounded_string(value, maximum: int, label: str) -> str:
    if (not isinstance(value, str) or len(value) > maximum or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise OperatorError(422, label + '格式不正确')
    return value


def normalized_configuration(update: dict, previous: dict) -> dict:
    if not isinstance(update, dict) or set(update) != set(_empty_config()):
        raise OperatorError(422, '配置字段不正确')
    result = _empty_config()
    for section, secret in SECRET_FIELDS.items():
        incoming, old = update[section], previous[section]
        allowed = set(result[section]) | {'clear_' + secret}
        if not isinstance(incoming, dict) or set(incoming) - allowed:
            raise OperatorError(422, '配置字段不正确')
        expected = set(result[section]) - {secret}
        if not expected <= set(incoming):
            raise OperatorError(422, '配置字段不完整')
        for field in expected:
            maximum = 2048 if field in ('url', 'base_url') else 200 if field == 'model' else 128
            result[section][field] = _bounded_string(incoming[field], maximum, field)
        clear = incoming.get('clear_' + secret, False)
        if not isinstance(clear, bool):
            raise OperatorError(422, '清除凭据标志必须为布尔值')
        supplied_value = incoming.get(secret, '')
        supplied = _bounded_string('' if supplied_value is None else supplied_value, 4096, '凭据')
        if supplied and (not supplied.isascii() or any(not 33 <= ord(char) <= 126 for char in supplied)):
            raise OperatorError(422, 'API Key 和认证 Token 只能包含无空白的可打印 ASCII 字符')
        if clear and supplied:
            raise OperatorError(422, '不能同时填写并清除同一凭据')
        result[section][secret] = '' if clear else supplied or old[secret]
        if section == 'model':
            provider = result[section]['provider']
            if provider not in ('', 'openai', 'anthropic', 'google'):
                raise OperatorError(422, '请选择支持的模型服务商')
            model = result[section]['model']
            if model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,199}', model):
                raise OperatorError(422, '模型名称格式不正确')
            address = result[section]['base_url']
            if provider == 'google' and address:
                raise OperatorError(422, 'Google 当前只支持固定官方接口，请留空 Base URL')
            changed = provider != old['provider'] or address != old['base_url']
        else:
            address = result[section]['url']
            changed = address != old['url']
        if changed and old[secret] and not supplied and not clear:
            raise OperatorError(422, '修改服务商或接口地址时，请重新填写或明确清除对应凭据')
        if address:
            field = 'base_url' if section == 'model' else 'url'
            result[section][field] = validate_public_url(address)
    ids = update['authorized_accounts']
    if not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(value, str) for value in ids):
        raise OperatorError(422, '最多授权 20 个独立 AI 模拟账户')
    try:
        result['authorized_accounts'] = list(dict.fromkeys(str(UUID(value)) for value in ids))
    except (ValueError, TypeError, AttributeError):
        raise OperatorError(422, '模拟账户编号格式不正确') from None
    return result


class EncryptedVault:
    def __init__(self, directory, *, read_only: bool = False, reader_group: int | None = None):
        self.directory = Path(directory)
        self.path, self.key_path = self.directory / 'config.sqlite3', self.directory / 'master.key'
        self.read_only = read_only
        if reader_group is not None and (type(reader_group) is not int or not 0 <= reader_group <= 2 ** 31 - 1):
            raise ValueError('Valid server-owned reader group required')
        self.reader_group = reader_group
        if read_only:
            if not self.path.is_file() or not self.key_path.is_file():
                raise OperatorError(503, '服务器配置库尚未准备好')
            return
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._permissions(self.directory, directory=True)
        if self.path.exists() and self.path.stat().st_size and not self.key_path.exists():
            raise OperatorError(503, '服务器加密密钥不可用，未覆盖配置')
        if not self.key_path.exists():
            descriptor = os.open(self.key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
        self._permissions(self.key_path)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self._permissions(self.path)
        with self.connect() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {'configuration', 'audit'}:
                raise OperatorError(503, '服务器配置库类型不匹配')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('CREATE TABLE IF NOT EXISTS configuration(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,value BLOB NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS audit(revision INTEGER PRIMARY KEY,at REAL NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL)')
            if not db.execute('SELECT 1 FROM configuration WHERE id=1').fetchone():
                db.execute('INSERT INTO configuration VALUES(1,0,?)', (self._cipher().encrypt(json.dumps(_empty_config()).encode()),))

    def _permissions(self, path: Path, *, directory: bool = False):
        if self.reader_group is not None:
            # Changing only a file's group to the process supplementary group is
            # allowed without CAP_CHOWN; owner and all account ledgers stay intact.
            os.chown(path, -1, self.reader_group)
        os.chmod(path, (0o750 if directory else 0o640) if self.reader_group is not None else (0o700 if directory else 0o600))

    def _cipher(self):
        try:
            if self.key_path.is_symlink() or self.path.is_symlink():
                raise ValueError('Symlink is not a protected vault')
            return Fernet(self.key_path.read_bytes())
        except (OSError, ValueError):
            raise OperatorError(503, '服务器加密配置不可用') from None

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5, isolation_level=None) if self.read_only else sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        if not self.read_only:
            db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    def _decode(self, row) -> dict:
        try:
            value = json.loads(self._cipher().decrypt(row['value']))
            if not isinstance(value, dict) or set(value) != set(_empty_config()):
                raise ValueError('Invalid protected configuration')
            return {'revision': row['revision'], **value}
        except (InvalidToken, ValueError, TypeError, KeyError):
            raise OperatorError(503, '服务器加密配置无法读取，未使用旧配置兜底') from None

    def read(self) -> dict:
        with self.connect() as db:
            return self._decode(db.execute('SELECT revision,value FROM configuration WHERE id=1').fetchone())

    def revision(self) -> int:
        with self.connect() as db:
            row = db.execute('SELECT revision FROM configuration WHERE id=1').fetchone()
            if row is None:
                raise OperatorError(503, '服务器配置库不可用')
            return row['revision']

    @staticmethod
    def _public(raw: dict) -> dict:
        result = {'revision': raw['revision'], 'authorized_accounts': raw['authorized_accounts'], 'trading_enabled': False}
        for section, secret in SECRET_FIELDS.items():
            result[section] = {key: value for key, value in raw[section].items() if key != secret}
            result[section][secret + '_configured'] = bool(raw[section][secret])
        return result

    def public(self) -> dict:
        return self._public(self.read())

    def save(self, expected_revision: int, config: dict, *, before_commit=None, actor='admin') -> dict:
        if self.read_only:
            raise OperatorError(403, '只读配置库不能写入')
        if type(expected_revision) is not int or expected_revision < 0:
            raise OperatorError(422, '配置版本格式不正确')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self._decode(db.execute('SELECT revision,value FROM configuration WHERE id=1').fetchone())
            if previous['revision'] != expected_revision:
                raise OperatorError(409, '配置已更新，请刷新后重新核对')
            updated = normalized_configuration(config, previous)
            encrypted = self._cipher().encrypt(json.dumps(updated, separators=(',', ':'), allow_nan=False).encode())
            if before_commit is not None:
                before_commit()
            db.execute('UPDATE configuration SET revision=?,value=? WHERE id=1', (expected_revision + 1, encrypted))
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (expected_revision + 1, time.time(), actor, 'CONFIGURATION_CHANGED'))
            db.commit()
        return self._public({'revision': expected_revision + 1, **updated})


class OperatorStore:
    def __init__(self, directory, *, vault=None, clock: Callable[[], float] = time.time,
                 secure: bool = True, session_seconds: int = 8 * 3600):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.path, self.auth_key = self.directory / 'settings.sqlite3', self.directory / 'session.key'
        reader_group = os.environ.get('FC666_OPERATOR_VAULT_READ_GROUP')
        if reader_group is not None and (not reader_group.isascii() or not reader_group.isdigit()):
            raise ValueError('FC666_OPERATOR_VAULT_READ_GROUP must be a server group ID')
        self.vault = vault or EncryptedVault(self.directory / 'vault', reader_group=int(reader_group) if reader_group is not None else None)
        self.clock, self.secure = clock, secure
        if not 1 <= session_seconds <= 8 * 3600:
            raise ValueError('Bounded operator session duration required')
        self.session_seconds, self.idle_seconds = session_seconds, min(1800, session_seconds)
        if not self.auth_key.exists():
            if self.path.exists() and self.path.stat().st_size:
                raise OperatorError(503, '管理员会话密钥不可用，未覆盖现有身份')
            descriptor = os.open(self.auth_key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(secrets.token_bytes(32))
        os.chmod(self.auth_key, 0o600)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {'administrator', 'sessions', 'bootstrap', 'attempts', 'auth_audit'}:
                raise OperatorError(503, '管理员数据库类型不匹配')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('CREATE TABLE IF NOT EXISTS administrator(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,salt BLOB NOT NULL,password_hash BLOB NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,principal TEXT NOT NULL,csrf_hash TEXT NOT NULL,created REAL NOT NULL,last_used REAL NOT NULL,expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS bootstrap(id INTEGER PRIMARY KEY CHECK(id=1),token_hash TEXT NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS attempts(bucket TEXT NOT NULL,at REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS auth_audit(id TEXT PRIMARY KEY,at REAL NOT NULL,action TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _password(password: str) -> bytes:
        if not isinstance(password, str) or not 12 <= len(password) or len(password.encode('utf-8')) > 128 or '\x00' in password:
            raise OperatorError(422, '密码至少 12 个字符，UTF-8 编码不超过 128 字节')
        return password.encode('utf-8')

    @staticmethod
    def _derive(password: bytes, salt: bytes) -> bytes:
        return hashlib.scrypt(password, salt=salt, n=2 ** 17, r=8, p=1, dklen=32, maxmem=256 * 1024 * 1024)

    @contextmanager
    def _hash_slot(self):
        if not HASH_SLOTS.acquire(blocking=False):
            raise OperatorError(429, '登录验证繁忙，请稍后重试')
        try:
            yield
        finally:
            HASH_SLOTS.release()

    def _limit(self, scope: str, subject: str):
        now = self.clock()
        buckets = ((scope + ':global', 12), (scope + ':' + _digest(subject), 5))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE at<?', (now - 900,))
            for bucket, maximum in buckets:
                if db.execute('SELECT COUNT(*) FROM attempts WHERE bucket=? AND at>?', (bucket, now - 300)).fetchone()[0] >= maximum:
                    raise OperatorError(429, '尝试次数过多，请稍后重试')
            for bucket, _ in buckets:
                db.execute('INSERT INTO attempts VALUES(?,?)', (bucket, now))
            db.commit()

    def initialized(self) -> bool:
        with self.connect() as db:
            return db.execute('SELECT 1 FROM administrator').fetchone() is not None

    def provision_setup(self, *, ttl_seconds: int = 1800) -> str:
        if not 60 <= ttl_seconds <= 3600:
            raise ValueError('Bounded setup invitation required')
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM administrator').fetchone():
                raise OperatorError(409, '管理员已创建，不生成新的初始化凭证')
            previous = db.execute('SELECT expires,used FROM bootstrap WHERE id=1').fetchone()
            if previous and not previous['used'] and previous['expires'] > self.clock():
                raise OperatorError(409, '初始化凭证仍有效，未覆盖现有凭证')
            db.execute('INSERT INTO bootstrap VALUES(1,?,?,0) ON CONFLICT(id) DO UPDATE SET token_hash=excluded.token_hash,expires=excluded.expires,used=0', (_digest(token), self.clock() + ttl_seconds))
            db.commit()
        return token

    def _csrf(self, token: str) -> str:
        try:
            key = self.auth_key.read_bytes()
            if len(key) != 32 or self.auth_key.is_symlink():
                raise ValueError('Invalid private session key')
            return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()
        except (OSError, ValueError):
            raise OperatorError(503, '管理员会话不可用') from None

    def _session(self, db, principal: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        db.execute('DELETE FROM sessions WHERE expires<=? OR last_used<=?', (now, now - self.idle_seconds))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', (_digest(token), principal, _digest(self._csrf(token)), now, now, now + self.session_seconds))
        db.execute('DELETE FROM sessions WHERE id IN (SELECT id FROM sessions WHERE principal=? ORDER BY created DESC LIMIT -1 OFFSET 10)', (principal,))
        return token

    def setup(self, setup_token: str, username: str, password: str) -> str:
        self._limit('setup', 'setup')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,31}', username or ''):
            raise OperatorError(422, '用户名需为 3–32 个字母、数字或 ._-')
        password_bytes = self._password(password)
        with self._hash_slot(), self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM bootstrap WHERE id=1').fetchone()
            if db.execute('SELECT 1 FROM administrator').fetchone():
                raise OperatorError(409, '管理员已创建，请使用登录')
            if (not SESSION_PATTERN.fullmatch(setup_token or '') or row is None or row['used']
                    or row['expires'] <= self.clock() or not hmac.compare_digest(_digest(setup_token), row['token_hash'])):
                raise OperatorError(401, '初始化凭证无效或已过期')
            salt, principal = secrets.token_bytes(16), str(uuid4())
            db.execute('INSERT INTO administrator VALUES(?,?,?,?)', (principal, username, salt, self._derive(password_bytes, salt)))
            db.execute('UPDATE bootstrap SET used=1 WHERE id=1')
            token = self._session(db, principal)
            db.execute('INSERT INTO auth_audit VALUES(?,?,?)', (str(uuid4()), self.clock(), 'ADMINISTRATOR_INITIALIZED'))
            db.commit()
        return token

    def login(self, username: str, password: str) -> str:
        self._limit('login', username)
        if not isinstance(password, str) or len(password.encode('utf-8')) > 128:
            raise OperatorError(401, '用户名或密码错误')
        with self._hash_slot(), self.connect() as db:
            row = db.execute('SELECT * FROM administrator WHERE username=?', (username,)).fetchone()
            salt = row['salt'] if row is not None else bytes(16)
            expected = row['password_hash'] if row is not None else bytes(32)
            checked = self._derive(password.encode('utf-8'), salt)
            if row is None or not hmac.compare_digest(checked, expected):
                raise OperatorError(401, '用户名或密码错误')
            db.execute('BEGIN IMMEDIATE')
            # A concurrent password rotation must make a checked old password unusable.
            current = db.execute('SELECT password_hash FROM administrator WHERE id=?', (row['id'],)).fetchone()
            if current is None or not hmac.compare_digest(current['password_hash'], expected):
                raise OperatorError(401, '用户名或密码错误')
            token = self._session(db, row['id'])
            db.execute('INSERT INTO auth_audit VALUES(?,?,?)', (str(uuid4()), self.clock(), 'LOGIN'))
            db.commit()
        return token

    def _authenticate(self, db, token: str | None):
        if not token or not SESSION_PATTERN.fullmatch(token):
            return None
        now = self.clock()
        row = db.execute('SELECT sessions.*,administrator.username FROM sessions JOIN administrator ON administrator.id=sessions.principal WHERE sessions.id=?', (_digest(token),)).fetchone()
        if row is None or row['expires'] <= now or row['last_used'] + self.idle_seconds <= now:
            return None
        csrf = self._csrf(token)
        if not hmac.compare_digest(row['csrf_hash'], _digest(csrf)):
            return None
        db.execute('UPDATE sessions SET last_used=? WHERE id=?', (now, _digest(token)))
        return {'username': row['username'], 'principal_id': row['principal'], 'csrf_token': csrf}

    def authenticate(self, token: str | None):
        with self.connect() as db:
            return self._authenticate(db, token)

    def save_configuration(self, token: str | None, csrf: str, expected_revision: int,
                           config: dict, *, before_commit=None, on_authenticated=None) -> dict:
        """Serialize credential admission with logout/password rotation.

        Lock order is auth -> vault. Callbacks must not authenticate again or
        otherwise acquire an auth write lock. Session and CSRF are checked after
        request-body parsing, then once more immediately before changing runtime.
        """
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            def authorized():
                identity = self._authenticate(db, token)
                if identity is None:
                    raise OperatorError(401, '管理员登录已失效，请重新登录')
                if not isinstance(csrf, str) or not hmac.compare_digest(csrf.encode(), identity['csrf_token'].encode()):
                    raise OperatorError(403, '操作验证已失效，请刷新后重试')
                return identity
            identity = authorized()
            def apply():
                current = authorized()
                if on_authenticated is not None:
                    on_authenticated(current)
                if before_commit is not None:
                    before_commit()
            result = self.vault.save(expected_revision, config,
                                     before_commit=apply, actor=identity['username'])
            db.commit()
            return result

    def require_admin(self, request, *, csrf: bool = False):
        identity = self.authenticate(request.cookies.get(COOKIE))
        if identity is None:
            raise OperatorError(401, '请先登录管理员')
        supplied = request.headers.get('x-csrf-token', '')
        if csrf and not hmac.compare_digest(supplied.encode(), identity['csrf_token'].encode()):
            raise OperatorError(403, '操作验证已失效，请刷新后重试')
        return identity

    def status(self, token: str | None = None) -> dict:
        identity = self.authenticate(token)
        return {'initialized': self.initialized(), 'authenticated': identity is not None,
                'username': identity['username'] if identity else None,
                'csrf_token': identity['csrf_token'] if identity else None,
                'config_revision': self.vault.revision(), 'trading_enabled': False}

    def logout(self, token: str):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM sessions WHERE id=?', (_digest(token),))
            db.commit()

    def change_password(self, token: str, current_password: str, new_password: str) -> str:
        identity = self.authenticate(token)
        if identity is None:
            raise OperatorError(401, '请先登录管理员')
        self._limit('password', identity['principal_id'])
        replacement = self._password(new_password)
        if not isinstance(current_password, str) or len(current_password.encode('utf-8')) > 128:
            raise OperatorError(401, '当前密码不正确')
        with self._hash_slot(), self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM administrator WHERE id=?', (identity['principal_id'],)).fetchone()
            active = db.execute('SELECT expires,last_used FROM sessions WHERE id=?', (_digest(token),)).fetchone()
            if (active is None or active['expires'] <= self.clock() or active['last_used'] + self.idle_seconds <= self.clock()
                    or row is None or not hmac.compare_digest(self._derive(current_password.encode(), row['salt']), row['password_hash'])):
                raise OperatorError(401, '当前密码不正确或登录已失效')
            salt = secrets.token_bytes(16)
            db.execute('UPDATE administrator SET salt=?,password_hash=? WHERE id=?', (salt, self._derive(replacement, salt), row['id']))
            db.execute('DELETE FROM sessions')
            replacement_token = self._session(db, row['id'])
            db.execute('INSERT INTO auth_audit VALUES(?,?,?)', (str(uuid4()), self.clock(), 'PASSWORD_CHANGED'))
            db.commit()
        return replacement_token
