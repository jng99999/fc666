"""Dedicated durable directional PAPER accounts; never touches legacy ledgers.

SQLite serializes each transition with BEGIN IMMEDIATE.  Versioned checkpoints
avoid replaying thousands of idle ticks on every read; immutable operation and
fill rows anchor the financial state and record decision/news/fill evidence.
After the bounded operation budget, only new risk stops.  Latest quote clocks
still advance and an existing position retains its protective exits.
"""
from contextlib import contextmanager
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from core.backtest.spot import digest
from core.paper.directional import Config, DirectionalPaper, News, Proposal, Quote, VERSION as ENGINE_VERSION, aware

VERSION = 'directional-sqlite-v1'
VOLATILE = {'last_quote', 'observed_at', 'recent_quotes', 'updated_at'}


class Conflict(ValueError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def core_hash(state):
    return digest({key: value for key, value in state.items() if key not in VOLATILE})


def identity(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or value != value.strip():
        raise ValueError('Bounded nonempty identity required')
    return value


class _CheckpointPaper(DirectionalPaper):
    """Resume the domain journal head without retaining prior events in memory."""

    def __init__(self, state):
        super().__init__(Config.model_validate(state['config']))
        self.balance = Decimal(state['balance'])
        self.fees = Decimal(state['fees'])
        self.gross_pnl = Decimal(state['gross_pnl'])
        self.halted = state['entry_halted']
        self.position = None if state['position'] is None else {
            key: value if key in ('direction', 'entry_decision_id') else Decimal(value)
            for key, value in state['position'].items()}
        self.pending = None if state['pending'] is None else Proposal.model_validate(state['pending'])
        self.pending_accepted_at = None if state['pending_accepted_at'] is None else aware_stamp(state['pending_accepted_at'])
        self.quote = None if state['last_quote'] is None else Quote.model_validate(state['last_quote'])
        self.observed_at = None if state['observed_at'] is None else aware_stamp(state['observed_at'])
        self.base_events = state['engine_events']
        self.base_head = state['journal_sha256']
        self.protection_only = state['status'] == 'STOPPED'

    def _record(self, kind, details):
        if kind == 'QUOTE':
            return  # Idle ticks belong in the checkpoint, not the trade budget.
        payload = {'seq': self.base_events + len(self.journal) + 1, 'kind': kind,
                   'details': details, 'previous_hash': self.journal[-1]['sha256'] if self.journal else self.base_head}
        self.journal.append({**payload, 'sha256': digest(payload)})


def aware_stamp(value):
    from datetime import datetime
    return aware(datetime.fromisoformat(value))


class Store:
    def __init__(self, path, *, max_operations=10000):
        if isinstance(max_operations, bool) or not isinstance(max_operations, int) or not 2 <= max_operations <= 10000:
            raise ValueError('Operation budget must be between 2 and 10000')
        self.max_operations = max_operations
        self.path = Path(path)
        if self.path.suffix != '.sqlite3':
            raise ValueError('A dedicated .sqlite3 file is required')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        with self.connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {'directional_accounts', 'directional_operations', 'directional_fills'}:
                raise ValueError('Refusing an unrelated database')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS directional_accounts(
                    account_id TEXT PRIMARY KEY, owner TEXT NOT NULL, create_key TEXT NOT NULL,
                    state TEXT NOT NULL, sha256 TEXT NOT NULL, UNIQUE(owner,create_key));
                CREATE TABLE IF NOT EXISTS directional_operations(
                    account_id TEXT NOT NULL REFERENCES directional_accounts(account_id),
                    revision INTEGER NOT NULL, request_key TEXT NOT NULL,
                    payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(account_id,revision), UNIQUE(account_id,request_key));
                CREATE TABLE IF NOT EXISTS directional_fills(
                    account_id TEXT NOT NULL REFERENCES directional_accounts(account_id),
                    ordinal INTEGER NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(account_id,ordinal));
            ''')
            for table in ('directional_operations', 'directional_fills'):
                for action in ('UPDATE', 'DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable directional evidence'); END")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _load(self, db, account_id, owner=None):
        row = db.execute('SELECT owner,state,sha256 FROM directional_accounts WHERE account_id=?', (identity(account_id),)).fetchone()
        if row is None or (owner is not None and row['owner'] != identity(owner)):
            raise KeyError(account_id)
        state = json.loads(row['state'])
        if (digest(state) != row['sha256'] or state['store_version'] != VERSION
                or state['engine_version'] != ENGINE_VERSION or state['account_id'] != account_id):
            raise ValueError('Directional checkpoint integrity failed')
        head = db.execute('SELECT revision,payload,sha256 FROM directional_operations WHERE account_id=? ORDER BY revision DESC LIMIT 1', (account_id,)).fetchone()
        if head is None:
            raise ValueError('Directional operation head missing')
        operation = json.loads(head['payload'])
        if (digest(operation) != head['sha256'] or head['revision'] != state['revision']
                or operation['core_state_sha256'] != core_hash(state)):
            raise ValueError('Directional operation/checkpoint mismatch')
        previous = db.execute('SELECT sha256 FROM directional_operations WHERE account_id=? AND revision=?', (account_id, state['revision'] - 1)).fetchone()
        if operation['previous_hash'] != (previous['sha256'] if previous else None):
            raise ValueError('Directional operation chain mismatch')
        engine = _CheckpointPaper(state)
        engine.view()  # Balance conservation also holds on restored checkpoints.
        fills = db.execute('SELECT count(*),max(ordinal) FROM directional_fills WHERE account_id=?', (account_id,)).fetchone()
        if fills[0] != state['fills_count'] or (fills[1] or 0) != state['fills_count']:
            raise ValueError('Directional fill count mismatch')
        return state, engine

    def _view(self, db, state, engine):
        result = engine.view()
        rows = db.execute('SELECT payload,sha256 FROM directional_fills WHERE account_id=? ORDER BY ordinal DESC LIMIT 1000', (state['account_id'],)).fetchall()
        fills = []
        for row in reversed(rows):
            fill = json.loads(row['payload'])
            if digest(fill) != row['sha256']:
                raise ValueError('Directional fill integrity failed')
            fills.append(fill)
        result.update({key: state[key] for key in ('account_id', 'revision', 'status', 'config', 'metadata',
                       'created_at', 'updated_at', 'last_quote', 'observed_at', 'recent_quotes', 'operation_limit', 'last_outcome')})
        result.update(fills=fills, fills_count=state['fills_count'], fills_truncated=state['fills_count'] > len(fills),
                      events=state['engine_events'], journal_sha256=state['journal_sha256'])
        return result

    def _checkpoint(self, state, engine, now):
        state = dict(state)
        view = engine.view()
        for event in engine.journal:
            kind, details = event['kind'], event['details']
            if kind == 'DECISION':
                state['last_outcome'] = {'kind': kind, 'status': details['status'], 'reason': details['reason'],
                                         'request_id': details['proposal']['request_id'], 'at': details['observed_at']}
            elif kind in ('ENTRY_REJECTED', 'ENTRY_STOPPED', 'FILL'):
                state['last_outcome'] = {'kind': kind, 'status': 'REJECTED' if kind == 'ENTRY_REJECTED' else
                                         'STOPPED' if kind == 'ENTRY_STOPPED' else 'FILLED',
                                         'reason': details['reason'], 'request_id': details.get('request_id'),
                                         'at': now.isoformat()}
        state.update(balance=view['balance'], fees=view['fees'], gross_pnl=view['gross_pnl'],
                     position=view['position'], pending=None if engine.pending is None else engine.pending.model_dump(mode='json'),
                     pending_accepted_at=None if engine.pending_accepted_at is None else engine.pending_accepted_at.isoformat(),
                     last_quote=None if engine.quote is None else engine.quote.model_dump(mode='json'),
                     observed_at=None if engine.observed_at is None else engine.observed_at.isoformat(),
                     entry_halted=engine.halted, engine_events=engine.base_events + len(engine.journal),
                     journal_sha256=engine.journal[-1]['sha256'] if engine.journal else engine.base_head,
                     updated_at=now.isoformat(), fills_count=state['fills_count'] + len(engine.fills))
        return state

    def _commit(self, db, state, engine, key, kind, details, now):
        old_head = db.execute('SELECT sha256 FROM directional_operations WHERE account_id=? ORDER BY revision DESC LIMIT 1', (state['account_id'],)).fetchone()
        if kind == 'DECISION':
            details = {**details, 'quote': None if engine.quote is None else engine.quote.model_dump(mode='json'),
                       'recent_quotes': state['recent_quotes']}
        state = self._checkpoint(state, engine, now)
        state['revision'] += 1
        operation = {'kind': kind, 'input': details, 'engine_events': engine.journal,
                     'core_state_sha256': core_hash(state), 'previous_hash': old_head['sha256'] if old_head else None}
        db.execute('INSERT INTO directional_operations VALUES(?,?,?,?,?)', (state['account_id'], state['revision'], key, encoded(operation), digest(operation)))
        for index, fill in enumerate(engine.fills, start=state['fills_count'] - len(engine.fills) + 1):
            db.execute('INSERT INTO directional_fills VALUES(?,?,?,?)', (state['account_id'], index, encoded(fill), digest(fill)))
        self._save(db, state)
        return state

    def _save(self, db, state):
        db.execute('UPDATE directional_accounts SET state=?,sha256=? WHERE account_id=?', (encoded(state), digest(state), state['account_id']))

    def _revision(self, state, expected_revision):
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValueError('Positive integer revision required')
        if state['revision'] != expected_revision:
            raise Conflict('Directional account revision conflict; reload current account')

    def _receipt(self, db, state, key, payload):
        row = db.execute('SELECT payload,sha256 FROM directional_operations WHERE account_id=? AND request_key=?', (state['account_id'], key)).fetchone()
        if row is None:
            return None
        receipt = json.loads(row['payload'])
        if digest(receipt) != row['sha256']:
            raise ValueError('Directional receipt integrity failed')
        if receipt['input']['request'] != payload:
            raise Conflict('Directional request ID reused with different parameters')
        return receipt

    def _stop_engine(self, state, engine, reason):
        state = dict(state)
        state['status'] = 'STOPPED'
        engine.halted = True
        engine.pending = None
        engine.pending_accepted_at = None
        engine.protection_only = True
        engine._record('ENTRY_STOPPED', {'reason': reason, 'protective_exits_enabled': True})
        return state

    def create(self, owner, config, request_id, now, metadata=None):
        owner, request_id, now = identity(owner), identity(request_id), aware(now)
        config = Config.model_validate(config)
        metadata = {} if metadata is None else json.loads(encoded(metadata))
        if not isinstance(metadata, dict) or len(encoded(metadata).encode()) > 8192:
            raise ValueError('Bounded JSON metadata object required')
        request = {'config': config.model_dump(mode='json'), 'metadata': metadata}
        with self.transaction() as db:
            existing = db.execute('SELECT account_id FROM directional_accounts WHERE owner=? AND create_key=?', (owner, request_id)).fetchone()
            if existing:
                state, engine = self._load(db, existing['account_id'], owner)
                self._receipt(db, state, 'create:' + request_id, request)
                return self._view(db, state, engine)
            if db.execute('SELECT count(*) FROM directional_accounts WHERE owner=?', (owner,)).fetchone()[0] >= 8:
                raise Conflict('At most eight directional accounts per browser session')
            if db.execute('SELECT count(*) FROM directional_accounts').fetchone()[0] >= 256:
                raise Conflict('Directional test account capacity reached')
            state = {'store_version': VERSION, 'engine_version': ENGINE_VERSION, 'account_id': str(uuid4()),
                     'revision': 0, 'status': 'RUNNING', 'config': request['config'], 'metadata': metadata,
                     'created_at': now.isoformat(), 'updated_at': now.isoformat(), 'balance': str(config.initial_cash),
                     'fees': '0', 'gross_pnl': '0', 'position': None, 'pending': None, 'pending_accepted_at': None,
                     'last_quote': None, 'observed_at': None, 'recent_quotes': [], 'entry_halted': False,
                     'engine_events': 0, 'journal_sha256': None, 'fills_count': 0, 'operation_limit': self.max_operations,
                     'last_outcome': None}
            db.execute('INSERT INTO directional_accounts VALUES(?,?,?,?,?)', (state['account_id'], owner, request_id, encoded(state), digest(state)))
            engine = _CheckpointPaper(state)
            state = self._commit(db, state, engine, 'create:' + request_id, 'CREATED', {'request': request, 'now': now.isoformat()}, now)
            return self._view(db, state, engine)

    def read(self, owner, account_id):
        with self.transaction() as db:
            state, engine = self._load(db, account_id, owner)
            return self._view(db, state, engine)

    def listing(self, owner):
        with self.transaction() as db:
            rows = db.execute('SELECT account_id FROM directional_accounts WHERE owner=? ORDER BY account_id', (identity(owner),)).fetchall()
            return [self._view(db, *self._load(db, row['account_id'], owner)) for row in rows]

    def active(self):
        with self.transaction() as db:
            rows = db.execute('SELECT account_id FROM directional_accounts ORDER BY account_id').fetchall()
            result = []
            for row in rows:
                state, engine = self._load(db, row['account_id'])
                if state['status'] == 'RUNNING' or engine.position is not None or engine.pending is not None:
                    result.append(self._view(db, state, engine))
            return result

    def submit(self, owner, account_id, proposal, expected_revision, now, news=None):
        proposal, now = Proposal.model_validate(proposal), aware(now)
        news = None if news is None else News.model_validate(news)
        request = proposal.model_dump(mode='json')
        key = 'decision:' + proposal.request_id
        with self.transaction() as db:
            state, engine = self._load(db, account_id, owner)
            receipt = self._receipt(db, state, key, request)
            if receipt is not None:
                return {**self._view(db, state, engine), 'decision_result': receipt['input']['result']}
            self._revision(state, expected_revision)
            if state['status'] == 'STOPPED' and proposal.target != 'FLAT':
                raise Conflict('Stopped directional account cannot open new risk')
            if state['status'] == 'STOPPED' and engine.position is None:
                raise Conflict('Stopped directional account has no position to reduce')
            if state['revision'] >= state['operation_limit'] and proposal.target != 'FLAT':
                state = self._stop_engine(state, engine, 'OPERATION_LIMIT')
                result = {'status': 'REJECTED', 'reason': 'ENTRY_HALTED'}
                state = self._commit(db, state, engine, key, 'DECISION', {'request': request, 'now': now.isoformat(),
                                     'news': None if news is None else news.model_dump(mode='json'), 'result': result}, now)
                return {**self._view(db, state, engine), 'decision_result': result}
            result = engine.submit(proposal, now=now, news=news)
            state = self._commit(db, state, engine, key, 'DECISION', {'request': request, 'now': now.isoformat(),
                                 'news': None if news is None else news.model_dump(mode='json'), 'result': result}, now)
            return {**self._view(db, state, engine), 'decision_result': result}

    def decision_request(self, owner, account_id, request_id):
        """Recover the originally accepted server clock on a transport retry."""
        with self.transaction() as db:
            self._load(db, account_id, owner)
            row = db.execute('SELECT payload,sha256 FROM directional_operations WHERE account_id=? AND request_key=?',
                             (account_id, 'decision:' + identity(request_id))).fetchone()
            if row is None:
                return None
            receipt = json.loads(row['payload'])
            if digest(receipt) != row['sha256']:
                raise ValueError('Directional receipt integrity failed')
            return receipt['input']['request']

    def submit_automatic(self, account_id, proposal, expected_revision, now, news=None):
        """Trusted worker entry; owner values never enter public account views."""
        with self.transaction() as db:
            state, _ = self._load(db, account_id)
            if state['metadata'].get('strategy') != 'SMA20_QUOTES' or state['status'] != 'RUNNING':
                raise Conflict('Automatic decision requires a running SMA20_QUOTES account')
            owner = db.execute('SELECT owner FROM directional_accounts WHERE account_id=?', (account_id,)).fetchone()['owner']
        # A simultaneous stop/quote bumps revision and safely rejects this signal.
        return self.submit(owner, account_id, proposal, expected_revision, now, news)

    def on_quote(self, account_id, quote, now, news=None):
        quote, now = Quote.model_validate(quote), aware(now)
        news = None if news is None else News.model_validate(news)
        request, key = quote.model_dump(mode='json'), 'quote:' + quote.at.isoformat()
        with self.transaction() as db:
            state, engine = self._load(db, account_id)
            if self._receipt(db, state, key, request) is not None:
                return self._view(db, state, engine)
            if engine.quote == quote:
                return self._view(db, state, engine)
            if state['status'] == 'RUNNING' and state['revision'] >= state['operation_limit']:
                state = self._stop_engine(state, engine, 'OPERATION_LIMIT')
            previous_core = core_hash(state)
            engine.on_quote(quote, now=now, news=news)
            previous_quote = None if state['last_quote'] is None else Quote.model_validate(state['last_quote'])
            gap = previous_quote is not None and (quote.at - previous_quote.at).total_seconds() > engine.config.quote_max_age_seconds
            recent = [] if gap else state['recent_quotes']
            state = {**state, 'recent_quotes': [*recent, request][-20:]}
            checkpoint = self._checkpoint(state, engine, now)
            if engine.journal or core_hash(checkpoint) != previous_core:
                state = self._commit(db, state, engine, key, 'QUOTE', {'request': request, 'now': now.isoformat(),
                                     'news': None if news is None else news.model_dump(mode='json')}, now)
            else:
                state = checkpoint
                self._save(db, state)  # Protection-only idle tick: no unbounded event append.
            return self._view(db, state, engine)

    def stop(self, owner, account_id, request_id, expected_revision, now):
        now, request_id = aware(now), identity(request_id)
        with self.transaction() as db:
            state, engine = self._load(db, account_id, owner)
            key = 'stop:' + request_id
            if self._receipt(db, state, key, {}) is not None:
                return self._view(db, state, engine)
            self._revision(state, expected_revision)
            if engine.observed_at is not None and now < engine.observed_at:
                raise ValueError('Observation clock cannot move backwards')
            if state['status'] == 'STOPPED':
                return self._view(db, state, engine)
            state = self._stop_engine(state, engine, 'USER_STOP')
            engine.observed_at = now
            state = self._commit(db, state, engine, key, 'ENTRY_STOPPED', {'request': {}, 'now': now.isoformat()}, now)
            return self._view(db, state, engine)
