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
from core.paper.price_action_rules import VERSION as RULE_VERSION, REQUIRED as RULE_REQUIRED

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
            if tables - {'directional_accounts', 'directional_operations', 'directional_fills', 'directional_rule_state'}:
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
                CREATE TABLE IF NOT EXISTS directional_rule_state(
                    account_id TEXT PRIMARY KEY REFERENCES directional_accounts(account_id),
                    payload TEXT NOT NULL, sha256 TEXT NOT NULL);
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
        try:
            checkpoint = self._rule_state(db, state['account_id'])
            result['rule_analysis'] = checkpoint.get('analysis')
            result['rule_history'] = checkpoint.get('history_barrier')
        except (ValueError, KeyError, TypeError):
            # A corrupt indicator checkpoint blocks new rule decisions, never
            # the existing financial ledger's protective quote transition.
            result['rule_analysis'] = {'rule_version': RULE_VERSION, 'state': 'UNAVAILABLE', 'samples': 0,
                'required': RULE_REQUIRED, 'target': None, 'available_at': None,
                'reason': 'RULE_CHECKPOINT_INVALID', 'evidence': {}}
            result['rule_history'] = None
        return result

    def _rule_state(self, db, account_id):
        row = db.execute('SELECT payload,sha256 FROM directional_rule_state WHERE account_id=?', (account_id,)).fetchone()
        if row is None:
            return {}
        value = json.loads(row['payload'])
        if digest(value) != row['sha256']:
            raise ValueError('Rule checkpoint integrity failed')
        if not isinstance(value, dict) or not isinstance(value.get('analysis'), dict):
            raise ValueError('Rule checkpoint shape failed')
        analysis = value['analysis']
        if (analysis.get('state') not in ('WARMUP', 'READY', 'UNAVAILABLE')
                or analysis.get('target') not in (None, 'LONG', 'SHORT')
                or not isinstance(analysis.get('rule_version'), str)
                or not isinstance(analysis.get('reason'), str) or not isinstance(analysis.get('evidence'), dict)
                or type(analysis.get('samples')) is not int or not 0 <= analysis['samples'] <= 500
                or analysis.get('required') != RULE_REQUIRED):
            raise ValueError('Rule checkpoint analysis failed')
        for stamp in (value.get('cursor'), analysis.get('available_at')):
            if stamp is not None:
                aware_stamp(stamp)
        barrier = value.get('history_barrier')
        if barrier is not None:
            if not isinstance(barrier, dict) or type(barrier.get('blocked')) is not bool:
                raise ValueError('Rule history barrier shape failed')
            for key in ('detected_at', 'resynchronized_at', 'resume_after'):
                if barrier.get(key) is not None:
                    aware_stamp(barrier[key])
        if value.get('cursor') is not None and (not isinstance(value.get('bar_sha256'), str)
                or len(value['bar_sha256']) != 64 or any(c not in '0123456789abcdef' for c in value['bar_sha256'])):
            raise ValueError('Rule checkpoint cursor hash failed')
        return value

    def rule_history_guard(self, account_id, history, *, now, restart_pending=frozenset()):
        """Persist revision fences and cancel only unfilled rule entry risk.

        The collector and quote worker call this while holding the history lock.
        Indicator persistence faults halt new risk; existing protections survive.
        """
        now = aware(now)
        with self.transaction() as db:
            state, engine = self._load(db, account_id)
            if state['metadata'].get('strategy') != 'PRICE_ACTION':
                return self._view(db, state, engine)
            invalid = False
            try:
                checkpoint = self._rule_state(db, account_id)
            except (ValueError, KeyError, TypeError):
                invalid, checkpoint = True, {}
            barrier = dict(checkpoint.get('history_barrier') or {})
            previous_barrier = dict(barrier)
            detected = history.get('revision_detected_at')
            synced = history.get('resynchronized_at')
            if detected is not None:
                detected = aware(detected)
                old = aware_stamp(barrier['detected_at']) if barrier.get('detected_at') else None
                if old is None or detected > old:
                    barrier.update(blocked=True, detected_at=detected.isoformat(),
                                   old_sha256=history.get('old_sha256') or barrier.get('old_sha256'),
                                   new_sha256=history.get('new_sha256') or barrier.get('new_sha256'))
                elif detected == old and history.get('new_sha256'):
                    barrier['new_sha256'] = history['new_sha256']
                if synced is not None:
                    synced, resume = aware(synced), aware(history['resume_after'])
                    old_sync = aware_stamp(barrier['resynchronized_at']) if barrier.get('resynchronized_at') else None
                    if synced >= detected and (old_sync is None or synced > old_sync):
                        old_resume = aware_stamp(barrier['resume_after']) if barrier.get('resume_after') else None
                        barrier.update(blocked=False, resynchronized_at=synced.isoformat(),
                                       resume_after=max(resume, old_resume).isoformat() if old_resume else resume.isoformat())
            reason = None
            if invalid:
                reason = 'RULE_CHECKPOINT_INVALID'
            elif history.get('error'):
                reason = history.get('error_reason') or 'HISTORY_UNAVAILABLE'
            elif barrier.get('blocked'):
                reason = 'HISTORY_RESYNC_PENDING'
            elif history.get('checked_at') is None:
                reason = 'HISTORY_UNAVAILABLE'
            pending = engine.pending
            if pending is not None and pending.target != 'FLAT':
                if (account_id, pending.request_id) in restart_pending:
                    reason = reason or 'HISTORY_RESTART_RECHECK'
                if detected is not None and engine.pending_accepted_at <= detected:
                    reason = reason or 'HISTORY_REVISED'
                if reason:
                    if engine.observed_at is not None and now < engine.observed_at:
                        raise ValueError('Observation clock cannot move backwards')
                    engine.pending, engine.pending_accepted_at, engine.observed_at = None, None, now
                    engine._record('ENTRY_REJECTED', {'request_id': pending.request_id, 'reason': reason,
                        'observed_at': now.isoformat(), 'history_barrier': barrier})
            if not invalid and barrier != previous_barrier:
                analysis = checkpoint.get('analysis') or {'rule_version': RULE_VERSION, 'state': 'UNAVAILABLE',
                    'samples': 0, 'required': RULE_REQUIRED, 'target': None, 'available_at': None,
                    'reason': 'HISTORY_RESYNC_PENDING', 'evidence': {}}
                if barrier.get('blocked'):
                    analysis = {**analysis, 'state': 'UNAVAILABLE', 'target': None, 'reason': 'HISTORY_RESYNC_PENDING'}
                value = {**checkpoint, 'analysis': analysis, 'history_barrier': barrier}
                db.execute('SAVEPOINT rule_history_metadata')
                try:
                    db.execute('INSERT INTO directional_rule_state VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload,sha256=excluded.sha256',
                               (account_id, encoded(value), digest(value)))
                    db.execute('RELEASE rule_history_metadata')
                except sqlite3.Error:
                    recovered_savepoint = False
                    if db.in_transaction:
                        try:
                            db.execute('ROLLBACK TO rule_history_metadata')
                            db.execute('RELEASE rule_history_metadata')
                            recovered_savepoint = True
                        except sqlite3.Error:
                            db.rollback()
                    if not recovered_savepoint:
                        # RAISE(ROLLBACK), and some SQLite IO failures, erase the
                        # whole transaction. Discard its in-memory journal and
                        # reload the latest financial head before recording halt.
                        db.execute('BEGIN IMMEDIATE')
                        state, engine = self._load(db, account_id)
                        pending = engine.pending
                        if pending is not None and pending.target != 'FLAT':
                            if engine.observed_at is not None and now < engine.observed_at:
                                raise ValueError('Observation clock cannot move backwards')
                            engine.pending, engine.pending_accepted_at, engine.observed_at = None, None, now
                            engine._record('ENTRY_REJECTED', {'request_id': pending.request_id,
                                'reason': 'RULE_HISTORY_STATE_UNAVAILABLE', 'observed_at': now.isoformat(),
                                'history_barrier': barrier})
                    if not engine.halted:
                        engine.halted, engine.protection_only = True, True
                        engine._record('ENTRY_STOPPED', {'reason': 'RULE_HISTORY_STATE_UNAVAILABLE',
                                                       'protective_exits_enabled': True})
            if engine.journal:
                state = self._commit(db, state, engine, 'history-guard:' + now.isoformat() + ':' + str(state['revision'] + 1), 'RULE_HISTORY_GUARD',
                                     {'now': now.isoformat(), 'history_barrier': barrier}, now)
            return self._view(db, state, engine)

    def cancel_pending_risk(self, account_id, now, news=None):
        """News/TTL controls run even during a quote outage; never invent a fill."""
        now = aware(now)
        news = None if news is None else News.model_validate(news)
        with self.transaction() as db:
            state, engine = self._load(db, account_id)
            engine.cancel_pending_risk(now=now, news=news)
            if engine.journal:
                state = self._commit(db, state, engine, 'gate:' + now.isoformat(), 'ENTRY_GATE',
                    {'now': now.isoformat(), 'news': None if news is None else news.model_dump(mode='json')}, now)
            else:
                state = self._checkpoint(state, engine, now)
                self._save(db, state)
            return self._view(db, state, engine)

    def rule_decision(self, account_id, analysis, *, bar_sha256, expected_revision, now, news=None, proposal=None):
        """Atomically persist one closed-bar observation and its optional proposal.

        Idle/blocked bars do not consume the financial-operation budget. Executed
        proposals retain the full signal evidence in the immutable operation log.
        """
        now = aware(now)
        news = None if news is None else News.model_validate(news)
        analysis = json.loads(encoded(analysis))
        if len(encoded(analysis)) > 32000 or len(bar_sha256) != 64:
            raise ValueError('Bounded rule evidence and bar hash required')
        with self.transaction() as db:
            state, engine = self._load(db, account_id)
            self._revision(state, expected_revision)
            if state['metadata'].get('strategy') != 'PRICE_ACTION' or state['status'] != 'RUNNING':
                raise Conflict('Rule decisions require an active closed-bar account')
            old = self._rule_state(db, account_id)
            stamp = analysis.get('available_at')
            if stamp is not None:
                closed_at = aware_stamp(stamp)
                if closed_at > now:
                    raise ValueError('Future rule evidence forbidden')
                if old.get('cursor') is not None:
                    previous = aware_stamp(old['cursor'])
                    if closed_at < previous:
                        return self._view(db, state, engine)
                    if closed_at == previous:
                        if old['bar_sha256'] != bar_sha256:
                            raise Conflict('A processed closed candle was revised')
                        if old.get('analysis', {}).get('state') == 'UNAVAILABLE':
                            value = {**old, 'analysis': {**analysis, 'decision_status': 'BAR_ALREADY_EVALUATED',
                                                        'evaluated_at': now.isoformat(), 'bar_sha256': bar_sha256}}
                            db.execute('UPDATE directional_rule_state SET payload=?,sha256=? WHERE account_id=?',
                                       (encoded(value), digest(value), account_id))
                        return self._view(db, state, engine)
            outcome = 'NO_SIGNAL'
            barrier = old.get('history_barrier') or {}
            if proposal is not None and Proposal.model_validate(proposal).target != 'FLAT':
                if barrier.get('blocked'):
                    analysis['decision_status'] = 'HISTORY_RESYNC_PENDING'
                    proposal = None
                elif barrier.get('resume_after') and (stamp is None or aware_stamp(stamp) <= aware_stamp(barrier['resume_after'])):
                    analysis['decision_status'] = 'HISTORY_RESYNC_WAIT_NEW_BAR'
                    proposal = None
            if proposal is not None:
                proposal = Proposal.model_validate(proposal)
                if state['revision'] >= state['operation_limit']:
                    state = self._stop_engine(state, engine, 'OPERATION_LIMIT')
                    outcome = 'ENTRY_HALTED'
                    state = self._commit(db, state, engine, 'rule-limit:' + proposal.request_id, 'RULE_LIMIT', {}, now)
                else:
                    result = engine.submit(proposal, now=now, news=news)
                    outcome = result['reason'] if result['status'] == 'REJECTED' else 'QUEUED'
                    state = self._commit(db, state, engine, 'decision:' + proposal.request_id, 'DECISION',
                        {'request': proposal.model_dump(mode='json'), 'result': result, 'now': now.isoformat(),
                         'news': None if news is None else news.model_dump(mode='json'),
                         'rule_analysis': analysis, 'bar_sha256': bar_sha256}, now)
            value = {**old, 'analysis': {**analysis, 'decision_status': analysis.get('decision_status', outcome),
                                      'evaluated_at': now.isoformat(), 'bar_sha256': bar_sha256}}
            if stamp is not None:
                value.update(cursor=stamp, bar_sha256=bar_sha256)
            db.execute('INSERT INTO directional_rule_state VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload,sha256=excluded.sha256',
                       (account_id, encoded(value), digest(value)))
            return self._view(db, state, _CheckpointPaper(state))

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
                                         'reason': details['reason'], 'request_id': details.get('request_id') or
                                         (state['pending']['request_id'] if kind == 'FILL' and details['reason'] in ('SIGNAL_ENTRY', 'SIGNAL_EXIT') and state['pending'] else None),
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

    def submit(self, owner, account_id, proposal, expected_revision, now, news=None, *, ai=False):
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
            if ai and (state['metadata'].get('strategy') != 'AI_RESEARCH' or state['metadata'].get('ai_paused', True)):
                raise Conflict('AI paper account is not enabled')
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
        receipt = self.decision_receipt(owner, account_id, request_id)
        return None if receipt is None else receipt['request']

    def decision_receipt(self, owner, account_id, request_id):
        """Read original admission evidence without driving an account transition."""
        with self.transaction() as db:
            self._load(db, account_id, owner)
            row = db.execute('SELECT payload,sha256 FROM directional_operations WHERE account_id=? AND request_key=?',
                             (account_id, 'decision:' + identity(request_id))).fetchone()
            if row is None:
                return None
            receipt = json.loads(row['payload'])
            if digest(receipt) != row['sha256']:
                raise ValueError('Directional receipt integrity failed')
            return receipt['input']

    def submit_automatic(self, account_id, proposal, expected_revision, now, news=None):
        """Trusted worker entry; owner values never enter public account views."""
        with self.transaction() as db:
            state, _ = self._load(db, account_id)
            if state['metadata'].get('strategy') != 'SMA20_QUOTES' or state['status'] != 'RUNNING':
                raise Conflict('Automatic decision requires a running SMA20_QUOTES account')
            owner = db.execute('SELECT owner FROM directional_accounts WHERE account_id=?', (account_id,)).fetchone()['owner']
        # A simultaneous stop/quote bumps revision and safely rejects this signal.
        return self.submit(owner, account_id, proposal, expected_revision, now, news)

    def submit_ai(self, owner, account_id, proposal, expected_revision, now, news=None):
        return self.submit(owner, account_id, proposal, expected_revision, now, news, ai=True)

    def decision_outcome(self, owner, account_id, request_id):
        """Recover a terminal request outcome even after later account events."""
        with self.transaction() as db:
            self._load(db, account_id, owner)
            row = db.execute('''SELECT o.payload,o.sha256 FROM directional_operations o,
                json_each(o.payload,'$.engine_events') e
                WHERE o.account_id=? AND json_extract(e.value,'$.details.request_id')=?
                AND json_extract(e.value,'$.kind') IN ('ENTRY_REJECTED','FILL')
                ORDER BY o.revision DESC LIMIT 1''', (account_id, identity(request_id))).fetchone()
            if row is None:
                return None
            value = json.loads(row['payload'])
            if digest(value) != row['sha256']:
                raise ValueError('Directional outcome integrity failed')
            for event in reversed(value['engine_events']):
                details = event['details']
                if event['kind'] in ('ENTRY_REJECTED', 'FILL') and details.get('request_id') == request_id:
                    return {'status': 'FILLED' if event['kind'] == 'FILL' else 'REJECTED', 'reason': details['reason']}

    def pause_ai(self, owner, account_id, request_id, expected_revision, now, paused):
        """Cancel unfilled AI intent atomically; never remove position protections."""
        now, request_id = aware(now), identity(request_id)
        with self.transaction() as db:
            state, engine = self._load(db, account_id, owner)
            request = {'paused': bool(paused)}
            key = 'ai-control:' + request_id
            if self._receipt(db, state, key, request) is not None:
                return self._view(db, state, engine)
            self._revision(state, expected_revision)
            if state['metadata'].get('strategy') != 'AI_RESEARCH':
                raise Conflict('A dedicated AI_RESEARCH account is required')
            if not paused and (state['status'] != 'RUNNING' or state['entry_halted']):
                raise Conflict('Stopped accounts cannot resume new risk')
            if engine.observed_at is not None and now < engine.observed_at:
                raise Conflict('Observation clock cannot move backwards')
            engine.pending = None
            engine.pending_accepted_at = None
            state['metadata'] = {**state['metadata'], 'ai_paused': bool(paused)}
            engine._record('AI_CONTROL', {'paused': bool(paused), 'protective_exits_enabled': True})
            state = self._commit(db, state, engine, key, 'AI_CONTROL',
                                 {'request': request, 'now': now.isoformat()}, now)
            return self._view(db, state, engine)

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
