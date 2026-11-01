"""Operator authentication, credential confidentiality and configuration admission."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import stat
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from apps.api.operator_settings import router_for
import apps.api.operator_settings as operator_routes
from core.security.operator import COOKIE, EncryptedVault, HASH_SLOTS, OperatorError, OperatorStore, validate_public_url

ORIGIN = 'https://fc666-test.vercel.app'
PASSWORD = 'Only-for-test-long-password-1!'
KEY = 'ONLY_TEST_MODEL_SECRET_DO_NOT_SERIALIZE'
NEWS_TOKEN = 'ONLY_TEST_NEWS_SECRET_DO_NOT_SERIALIZE'


def public_dns(*args, **kwargs):
    return [(2, 1, 6, '', ('1.1.1.1', 443))]


def configuration(revision=0, **patch):
    value = {'expected_revision': revision,
             'model': {'provider': 'openai', 'model': 'fixture-model', 'base_url': '', 'api_key': KEY},
             'news': {'source': '', 'url': '', 'token': ''},
             'gold': {'source': '', 'url': '', 'token': ''}, 'authorized_accounts': []}
    value.update(patch)
    return value


@pytest.fixture
def operator(tmp_path):
    now = [1000.0]
    store = OperatorStore(tmp_path / 'operator', clock=lambda: now[0])
    setup_token = store.provision_setup()
    app = FastAPI()
    app.include_router(router_for(store, [ORIGIN]))
    client = TestClient(app, base_url=ORIGIN)
    return store, setup_token, client, now


def initialize(operator):
    store, token, client, _ = operator
    response = client.post('/api/v1/directional/admin/setup', json={'setup_token': token, 'username': 'operator', 'password': PASSWORD}, headers={'Origin': ORIGIN})
    assert response.status_code == 200, response.text
    return response


def csrf(client):
    return {'Origin': ORIGIN, 'X-CSRF-Token': client.get('/api/v1/directional/admin/status').json()['csrf_token']}


def test_bootstrap_is_private_single_use_and_session_cookie_is_protected(operator):
    store, invitation, client, _ = operator
    status = client.get('/api/v1/directional/admin/status')
    assert status.status_code == 200
    assert status.json() == {'initialized': False, 'authenticated': False, 'username': None, 'csrf_token': None, 'config_revision': 0, 'trading_enabled': False}
    assert invitation not in status.text
    assert client.get('/api/v1/directional/admin/configuration').status_code == 401
    assert client.get('/api/v1/directional/admin/accounts').status_code == 401
    with pytest.raises(OperatorError) as error:
        store.provision_setup()
    assert error.value.status == 409
    response = initialize(operator)
    cookie = response.headers['set-cookie']
    assert all(flag in cookie for flag in ['HttpOnly', 'Secure', 'SameSite=strict', 'Path=/', 'Max-Age=28800'])
    assert response.json()['authenticated'] is True
    assert len(client.cookies.get(COOKIE)) == 43
    assert len(response.json()['csrf_token']) == 64
    assert client.cookies.get(COOKIE) not in response.text
    repeated = client.post('/api/v1/directional/admin/setup', json={'setup_token': invitation, 'username': 'another', 'password': PASSWORD}, headers={'Origin': ORIGIN})
    assert repeated.status_code == 409
    assert client.get('/api/v1/directional/admin/status').json()['username'] == 'operator'


def test_bootstrap_expiration_and_invalid_invitation_do_not_create_admin(operator):
    store, token, client, now = operator
    now[0] += 1800
    expired = client.post('/api/v1/directional/admin/setup', json={'setup_token': token, 'username': 'operator', 'password': PASSWORD}, headers={'Origin': ORIGIN})
    assert expired.status_code == 401
    assert store.initialized() is False
    replacement = store.provision_setup()
    assert replacement != token
    assert token not in store.path.read_bytes().decode(errors='ignore')


def test_write_boundaries_reject_cross_origin_wrong_type_size_and_duplicate_keys(operator):
    _, token, client, _ = operator
    body = {'setup_token': token, 'username': 'operator', 'password': PASSWORD}
    for headers in [{}, {'Origin': 'https://evil.example'}, {'Origin': ORIGIN, 'Sec-Fetch-Site': 'cross-site'}]:
        assert client.post('/api/v1/directional/admin/setup', json=body, headers=headers).status_code == 403
    assert client.post('/api/v1/directional/admin/setup', content=json.dumps(body), headers={'Origin': ORIGIN, 'Content-Type': 'text/plain'}).status_code == 415
    too_large = json.dumps({'password': 'PRIVATE_' * 3000})
    response = client.post('/api/v1/directional/admin/setup', content=too_large, headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})
    assert response.status_code == 413
    duplicate = '{"username":"operator","username":"other","password":"' + PASSWORD + '","setup_token":"' + token + '"}'
    response = client.post('/api/v1/directional/admin/setup', content=duplicate, headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert PASSWORD not in response.text and token not in response.text
    assert client.get('/api/v1/directional/admin/status').json()['initialized'] is False


def test_configuration_requires_login_and_csrf_and_never_serializes_credentials(operator):
    store, _, client, _ = operator
    initialize(operator)
    for headers in [{'Origin': ORIGIN}, {'Origin': ORIGIN, 'X-CSRF-Token': 'wrong'}]:
        assert client.post('/api/v1/directional/admin/configuration', json=configuration(), headers=headers).status_code == 403
    response = client.post('/api/v1/directional/admin/configuration', json=configuration(), headers=csrf(client))
    assert response.status_code == 200
    public = response.json()
    assert public['revision'] == 1 and public['model']['api_key_configured'] is True
    assert 'api_key' not in public['model'] and 'token' not in public['news']
    assert KEY not in response.text and KEY not in client.get('/api/v1/directional/admin/configuration').text
    for path in store.directory.rglob('*'):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()
            assert PASSWORD.encode() not in path.read_bytes()
    raw = store.vault.read()
    assert raw['model']['api_key'] == KEY
    stale = client.post('/api/v1/directional/admin/configuration', json=configuration(), headers=csrf(client))
    assert stale.status_code == 409 and KEY not in stale.text
    live = configuration(1, trading_enabled=True)
    assert client.post('/api/v1/directional/admin/configuration', json=live, headers=csrf(client)).status_code == 422
    assert store.vault.revision() == 1


def test_secret_retention_clear_and_host_change_cannot_leak_old_credentials(operator, monkeypatch):
    monkeypatch.setattr('core.security.operator.socket.getaddrinfo', public_dns)
    store, _, client, _ = operator
    initialize(operator)
    first = configuration(news={'source': 'fixture-calendar', 'url': 'https://calendar.example/api', 'token': NEWS_TOKEN})
    assert client.post('/api/v1/directional/admin/configuration', json=first, headers=csrf(client)).status_code == 200
    next_value = configuration(1)
    next_value['model'].pop('api_key')
    next_value['news'] = {'source': 'fixture-calendar', 'url': 'https://calendar.example/api', 'token': ''}
    response = client.post('/api/v1/directional/admin/configuration', json=next_value, headers=csrf(client))
    assert response.status_code == 200 and response.json()['news']['token_configured'] is True
    assert store.vault.read()['model']['api_key'] == KEY
    changed = configuration(2)
    changed['model'].pop('api_key')
    changed['model']['base_url'] = 'https://other.example/v1'
    response = client.post('/api/v1/directional/admin/configuration', json=changed, headers=csrf(client))
    assert response.status_code == 422 and store.vault.revision() == 2
    cleared = configuration(2)
    cleared['model'].update(api_key='', clear_api_key=True)
    cleared['news'] = {'source': 'fixture-calendar', 'url': 'https://calendar.example/api', 'clear_token': True}
    response = client.post('/api/v1/directional/admin/configuration', json=cleared, headers=csrf(client))
    assert response.status_code == 200 and response.json()['model']['api_key_configured'] is False
    assert store.vault.read()['news']['token'] == ''


@pytest.mark.parametrize('url', ['http://example.com', 'https://localhost', 'https://127.0.0.1', 'https://user:secret@example.com', 'https://example.com/?api_key=secret', 'https://example.com/#secret', 'https://metadata.internal', 'https://example.com\\@127.0.0.1', 'https://example.com:0', 'https://metadata.internal.'])
def test_untrusted_url_forms_are_rejected(url):
    def resolver(host, port, **kwargs):
        return [(2, 1, 6, '', (host if host == '127.0.0.1' else '1.1.1.1', port))]
    with pytest.raises(OperatorError) as error:
        validate_public_url(url, resolver)
    assert error.value.status == 422
    assert 'secret' not in error.value.message


def test_mixed_dns_and_metadata_addresses_rejected_and_google_custom_base_forbidden(operator, monkeypatch):
    for ip in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:169.254.169.254', 'fe80::1', 'fc00::1']:
        with pytest.raises(OperatorError):
            validate_public_url('https://example.com', lambda *args, **kwargs: public_dns() + [(2, 1, 6, '', (ip, 443))])
    monkeypatch.setattr('core.security.operator.socket.getaddrinfo', public_dns)
    _, _, client, _ = operator
    initialize(operator)
    value = configuration()
    value['model'].update(provider='google', base_url='https://example.com')
    response = client.post('/api/v1/directional/admin/configuration', json=value, headers=csrf(client))
    assert response.status_code == 422
    assert KEY not in response.text


def test_restart_readonly_persistence_and_expired_session(operator):
    store, _, client, now = operator
    initialize(operator)
    assert client.post('/api/v1/directional/admin/configuration', json=configuration(), headers=csrf(client)).status_code == 200
    token = client.cookies.get(COOKIE)
    reopened = OperatorStore(store.directory, clock=lambda: now[0])
    assert reopened.authenticate(token)['username'] == 'operator'
    readonly = EncryptedVault(store.vault.directory, read_only=True)
    before = {path.name: path.stat().st_mtime_ns for path in readonly.directory.iterdir()}
    assert readonly.read()['model']['api_key'] == KEY
    assert {path.name: path.stat().st_mtime_ns for path in readonly.directory.iterdir()} == before
    with pytest.raises(OperatorError) as error:
        readonly.save(1, configuration())
    assert error.value.status == 403
    now[0] += 1801
    assert reopened.authenticate(token) is None
    assert client.get('/api/v1/directional/admin/configuration').status_code == 401


def test_password_rotation_revokes_other_sessions_and_logout_revokes_current(operator):
    store, _, client, _ = operator
    initialize(operator)
    old_token = client.cookies.get(COOKIE)
    another = store.login('operator', PASSWORD)
    changed = client.post('/api/v1/directional/admin/password', json={'current_password': PASSWORD, 'new_password': 'The-new-long-test-password-2!'}, headers=csrf(client))
    assert changed.status_code == 200 and changed.json()['authenticated'] is True
    assert client.cookies.get(COOKIE) != old_token
    assert store.authenticate(old_token) is None and store.authenticate(another) is None
    for path in store.directory.rglob('*'):
        if path.is_file():
            assert b'The-new-long-test-password-2!' not in path.read_bytes()
    current = client.cookies.get(COOKIE)
    response = client.post('/api/v1/directional/admin/logout', json={}, headers=csrf(client))
    assert response.status_code == 200 and response.json()['authenticated'] is False
    assert store.authenticate(current) is None


def test_persistent_rate_limit_and_single_hash_slot(operator):
    store, _, client, now = operator
    initialize(operator)
    for _ in range(5):
        assert client.post('/api/v1/directional/admin/login', json={'username': 'operator', 'password': 'incorrect'}, headers={'Origin': ORIGIN}).status_code == 401
    reopened = OperatorStore(store.directory, clock=lambda: now[0])
    with pytest.raises(OperatorError) as error:
        reopened.login('operator', PASSWORD)
    assert error.value.status == 429
    now[0] += 301
    with HASH_SLOTS:
        response = client.post('/api/v1/directional/admin/login', json={'username': 'operator', 'password': PASSWORD}, headers={'Origin': ORIGIN})
        assert response.status_code == 429
    assert KEY not in response.text and PASSWORD not in response.text


def test_cas_callbacks_are_atomic_fail_closed_and_account_validation_is_authenticated(operator):
    store, _, client, _ = operator
    initialize(operator)
    calls = []
    def validate(request, ids):
        calls.append(('validate', tuple(ids)))
        if ids:
            raise OperatorError(403, '仅可授权当前会话拥有的 AI 模拟账户')
    def pause():
        calls.append(('pause',))
    app = FastAPI()
    app.include_router(router_for(store, [ORIGIN], validate_accounts=validate, on_change=pause,
                                  accounts=lambda request: {'accounts': [], 'trading_enabled': False}))
    guarded = TestClient(app, base_url=ORIGIN)
    guarded.cookies.set(COOKIE, client.cookies.get(COOKIE))
    invalid = configuration(authorized_accounts=['12345678-1234-4234-8234-123456789012'])
    assert guarded.post('/api/v1/directional/admin/configuration', json=invalid, headers=csrf(guarded)).status_code == 403
    assert store.vault.revision() == 0 and calls == [('validate', tuple(invalid['authorized_accounts']))]
    calls.clear()
    assert guarded.post('/api/v1/directional/admin/configuration', json=configuration(), headers=csrf(guarded)).status_code == 200
    assert calls == [('validate', ()), ('pause',)]
    assert guarded.get('/api/v1/directional/admin/accounts').json() == {'accounts': [], 'trading_enabled': False}
    def fail():
        raise OperatorError(503, '暂停失败')
    old = store.vault.read()
    with pytest.raises(OperatorError):
        body = configuration(1);body.pop('expected_revision')
        store.vault.save(1, body, before_commit=fail)
    assert store.vault.read() == old


def test_concurrent_cas_never_overwrites_and_missing_key_never_reinitializes(operator):
    store, _, _, _ = operator
    entered, release = threading.Event(), threading.Event()
    def first():
        body = configuration();body.pop('expected_revision')
        return store.vault.save(0, body, before_commit=lambda: (entered.set(), release.wait(3)))
    def second():
        body = configuration();body.pop('expected_revision')
        try:
            return store.vault.save(0, body)
        except OperatorError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        assert entered.wait(2)
        b = pool.submit(second)
        release.set()
        assert a.result()['revision'] == 1 and b.result() == 409
    key = store.vault.key_path.read_bytes()
    store.vault.key_path.unlink()
    with pytest.raises(OperatorError):
        EncryptedVault(store.vault.directory)
    assert not store.vault.key_path.exists()
    store.vault.key_path.write_bytes(key)
    assert store.vault.read()['revision'] == 1


def test_concurrent_setup_creates_only_one_administrator_and_corrupted_ciphertext_fails_closed(operator):
    store, invitation, _, _ = operator
    barrier = threading.Barrier(2)
    def create(username):
        barrier.wait()
        try:
            return store.setup(invitation, username, PASSWORD)
        except OperatorError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(create, 'operator_one'), pool.submit(create, 'operator_two')
        results = [a.result(), b.result()]
    assert sum(isinstance(value, str) for value in results) == 1
    assert any(value in (409, 429) for value in results if isinstance(value, int))
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM administrator').fetchone()[0] == 1
    with store.vault.connect() as db:
        db.execute('UPDATE configuration SET value=? WHERE id=1', (b'corrupt_ciphertext',))
    with pytest.raises(OperatorError) as error:
        store.vault.read()
    assert error.value.status == 503
    assert KEY not in error.value.message


@pytest.mark.parametrize('secret', ['a b', 'a\tb', 'a\nb', '密钥', 'token🙂', '\x7f', ' key'])
def test_secret_fields_reject_non_ascii_and_whitespace(operator, secret):
    store, _, client, _ = operator
    initialize(operator)
    body = configuration()
    body['model']['api_key'] = secret
    response = client.post('/api/v1/directional/admin/configuration', json=body, headers=csrf(client))
    assert response.status_code == 422
    assert store.vault.revision() == 0
    assert secret not in response.text


@pytest.mark.parametrize('revocation', ['logout', 'password'])
def test_body_parsing_cannot_preserve_revoked_configuration_admission(operator, monkeypatch, revocation):
    store, _, client, _ = operator
    initialize(operator)
    token = client.cookies.get(COOKIE)
    headers = csrf(client)
    original = operator_routes._body
    async def revoke_after_parse(request, schema):
        value = await original(request, schema)
        if schema is operator_routes.Configuration:
            if revocation == 'logout':
                store.logout(token)
            else:
                store.change_password(token, PASSWORD, 'Rotated-after-body-reading-2!')
        return value
    monkeypatch.setattr(operator_routes, '_body', revoke_after_parse)
    response = client.post('/api/v1/directional/admin/configuration', json=configuration(), headers=headers)
    assert response.status_code == 401
    assert store.vault.revision() == 0
    assert store.authenticate(token) is None


def test_configuration_holds_auth_before_vault_and_logout_serializes_after_it(operator):
    store, _, client, _ = operator
    initialize(operator)
    token, anti_csrf = client.cookies.get(COOKIE), csrf(client)['X-CSRF-Token']
    entered, release, logout_started, logout_done = (threading.Event() for _ in range(4))
    body = configuration();body.pop('expected_revision')
    def save():
        return store.save_configuration(token, anti_csrf, 0, body,
                                        before_commit=lambda: (entered.set(), release.wait(3)))
    def revoke():
        logout_started.set();store.logout(token);logout_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(save);assert entered.wait(2)
        b = pool.submit(revoke);assert logout_started.wait(2)
        assert not logout_done.wait(.1), 'logout cannot revoke in the middle of an admitted configuration commit'
        release.set()
        assert a.result()['revision'] == 1
        b.result()
    assert store.authenticate(token) is None
    with pytest.raises(OperatorError) as error:
        store.save_configuration(token, anti_csrf, 1, body)
    assert error.value.status == 401 and store.vault.revision() == 1


def test_revocation_committed_first_makes_waiting_save_fail_without_runtime_callback(operator):
    store, _, client, _ = operator
    initialize(operator)
    token, anti_csrf = client.cookies.get(COOKIE), csrf(client)['X-CSRF-Token']
    body = configuration();body.pop('expected_revision')
    started, calls = threading.Event(), []
    def save():
        started.set()
        try:
            return store.save_configuration(token, anti_csrf, 0, body, before_commit=lambda: calls.append('runtime'))
        except OperatorError as error:
            return error.status
    with store.connect() as db, ThreadPoolExecutor(max_workers=1) as pool:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM sessions')
        future = pool.submit(save);assert started.wait(2)
        db.commit()
        assert future.result() == 401
    assert calls == [] and store.vault.revision() == 0


def test_rotated_session_requires_new_csrf_for_configuration(operator):
    store, _, client, _ = operator
    initialize(operator)
    old_token, old_csrf = client.cookies.get(COOKIE), csrf(client)['X-CSRF-Token']
    new_token = store.change_password(old_token, PASSWORD, 'New-password-for-CSRF-rotation-2!')
    body = configuration();body.pop('expected_revision')
    with pytest.raises(OperatorError) as error:
        store.save_configuration(new_token, old_csrf, 0, body)
    assert error.value.status == 403 and store.vault.revision() == 0
    fresh = store.authenticate(new_token)['csrf_token']
    assert store.save_configuration(new_token, fresh, 0, body)['revision'] == 1


def test_reader_group_exposes_only_vault_and_preserves_permissions_after_save(tmp_path, monkeypatch):
    group = os.getgid()
    monkeypatch.setenv('FC666_OPERATOR_VAULT_READ_GROUP', str(group))
    store = OperatorStore(tmp_path / 'operator')
    vault = store.vault
    owner = os.getuid()
    assert stat.S_IMODE(vault.directory.stat().st_mode) == 0o750
    assert vault.directory.stat().st_gid == group and vault.directory.stat().st_uid == owner
    for path in [vault.path, vault.key_path]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert path.stat().st_gid == group and path.stat().st_uid == owner
    body = configuration();body.pop('expected_revision')
    assert vault.save(0, body)['revision'] == 1
    before = {path.name: (path.stat().st_mode, path.stat().st_uid, path.stat().st_gid, path.stat().st_mtime_ns) for path in vault.directory.iterdir()}
    readonly = EncryptedVault(vault.directory, read_only=True)
    assert readonly.read()['model']['api_key'] == KEY
    assert {path.name: (path.stat().st_mode, path.stat().st_uid, path.stat().st_gid, path.stat().st_mtime_ns) for path in vault.directory.iterdir()} == before
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.auth_key.stat().st_mode) == 0o600
    reopened = OperatorStore(store.directory)
    assert reopened.vault.read()['revision'] == 1
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o640 for path in [vault.path, vault.key_path])
