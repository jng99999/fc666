"""Separate durable, owner-scoped USD ledger with immutable event receipts."""
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from core.gold.market import Quote, SOURCE, analysis, sampled_bars


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS quotes(ts INTEGER PRIMARY KEY,bid TEXT NOT NULL,ask TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,owner TEXT NOT NULL,state TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS account_owner ON accounts(owner);
                CREATE TABLE IF NOT EXISTS events(account_id TEXT NOT NULL,seq INTEGER NOT NULL,key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,payload TEXT NOT NULL,hash TEXT NOT NULL,
                    PRIMARY KEY(account_id,seq),UNIQUE(account_id,key));
                CREATE TRIGGER IF NOT EXISTS gold_events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'immutable ledger'); END;
                CREATE TRIGGER IF NOT EXISTS gold_events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'immutable ledger'); END;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def put_quote(self, quote, now):
        quote.validate(now)
        with self.connect() as db:
            latest = db.execute('SELECT max(ts) FROM quotes').fetchone()[0]
            if latest is not None and quote.ts < latest:
                raise ValueError('Gold quote timestamp moved backwards')
            old = db.execute('SELECT bid,ask FROM quotes WHERE ts=?', (quote.ts,)).fetchone()
            if old and (Decimal(old['bid']) != quote.bid or Decimal(old['ask']) != quote.ask):
                raise ValueError('Conflicting gold quote timestamp')
            db.execute('INSERT OR IGNORE INTO quotes VALUES(?,?,?)', (quote.ts, str(quote.bid), str(quote.ask)))

    def market(self, now):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM quotes ORDER BY ts DESC LIMIT 6000').fetchall()
        quotes = [Quote(r['ts'], Decimal(r['bid']), Decimal(r['ask'])) for r in reversed(rows)]
        quote = quotes[-1] if quotes else None
        fresh = False
        if quote:
            try:
                quote.validate(now)
                fresh = True
            except ValueError:
                pass
        bars = sampled_bars(quotes, now)
        calculated = analysis(bars)
        if calculated.get('available_at') is not None and now-calculated['available_at'] > 90000:
            calculated = {'status': 'STALE', 'bars': 0, 'required': 20, 'sma20': None}
        return {'quote': quote.json() if quote else None, 'fresh': fresh,
                'bars': bars[-120:], 'analysis': calculated, 'trading_enabled': False,
                'sampling': '10s bid/ask snapshots; 1m sampled mid bars; no exchange volume',
                'source': SOURCE, 'checked_at': now}

    def _read(self, db, identity, owner=None):
        row = db.execute('SELECT owner,state FROM accounts WHERE id=?', (identity,)).fetchone()
        if row is None or (owner is not None and row['owner'] != owner):
            raise KeyError(identity)
        state = json.loads(row['state'])
        last = db.execute('SELECT payload,hash FROM events WHERE account_id=? ORDER BY seq DESC LIMIT 1', (identity,)).fetchone()
        if last is None or digest(json.loads(last['payload'])) != last['hash'] or json.loads(last['payload'])['state'] != state:
            raise ValueError('Gold ledger state integrity failed')
        return state

    def _write(self, db, state, key, request_hash, kind, now, details):
        previous = db.execute('SELECT hash FROM events WHERE account_id=? ORDER BY seq DESC LIMIT 1', (state['id'],)).fetchone()
        state['revision'] += 1
        state['updated_at'] = now
        payload = {'state': state, 'kind': kind, 'details': details, 'previous_hash': previous['hash'] if previous else None}
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                   (state['id'], state['revision'], key, request_hash, canonical(payload), digest(payload)))
        db.execute('UPDATE accounts SET state=? WHERE id=?', (canonical(state), state['id']))

    def create(self, owner, config, request_id, now):
        identity = str(uuid4())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT account_id,seq,request_hash,payload,hash FROM events WHERE key=?', (request_id,)).fetchall()
            policy_matches = []
            for receipt in existing:
                try:
                    state = self._read(db, receipt['account_id'], owner)
                except KeyError:
                    continue
                if config['mode'] == 'POLICY':
                    payload = json.loads(receipt['payload'])
                    original = payload['state']
                    if (payload['kind'] != 'CREATED' or state['mode'] != 'POLICY' or original['mode'] != 'POLICY'
                            or original['config'] != config or receipt['request_hash'] != digest(config)
                            or receipt['request_hash'] != digest(original['config'])):
                        raise Conflict('Create request ID reused with different policy parameters')
                    if (digest(payload) != receipt['hash'] or original['id'] != receipt['account_id']
                            or original['revision'] != receipt['seq'] or receipt['seq'] != 1
                            or payload['previous_hash'] is not None):
                        raise ValueError('Gold policy creation receipt integrity failed')
                    policy_matches.append(state)
                    continue
                if receipt['request_hash'] != digest(config):
                    raise Conflict('Create request ID was reused with different parameters')
                return state
            if len(policy_matches) > 1:
                raise Conflict('Create request ID matches multiple policy accounts')
            if policy_matches:
                return policy_matches[0]
            if db.execute('SELECT count(*) FROM accounts WHERE owner=?', (owner,)).fetchone()[0] >= 8:
                raise Conflict('At most eight gold accounts per browser session')
            if db.execute('SELECT count(*) FROM accounts').fetchone()[0] >= 256:
                raise Conflict('Gold test environment account capacity reached')
            state = {'id': identity, 'revision': 0, 'created_at': now, 'updated_at': now,
                     'status': 'ACTIVE', 'mode': config['mode'], 'config': config,
                     'cash': config['initial_cash'], 'quantity': '0', 'basis': '0', 'fees': '0',
                     'realized': '0', 'intent': None, 'last_bar': None, 'phase': 'WAITING',
                     'currency': 'USD', 'instrument': 'XAU/USD', 'trading_enabled': False}
            db.execute('INSERT INTO accounts VALUES(?,?,?)', (identity, owner, canonical(state)))
            self._write(db, state, request_id, digest(config), 'CREATED', now, {'source': SOURCE})
            return state

    def accounts(self, owner):
        with self.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM accounts WHERE owner=? ORDER BY rowid DESC LIMIT 8', (owner,))]
            return [self._read(db, identity, owner) for identity in ids]

    def read(self, identity, owner, before=None):
        with self.connect() as db:
            state = self._read(db, identity, owner)
            receipts = db.execute('SELECT seq,payload,hash FROM events WHERE account_id=? AND seq<? ORDER BY seq DESC LIMIT 100', (identity, before if before is not None else state['revision']+1)).fetchall()
        events = [{'seq': r['seq'], 'hash': r['hash'], 'kind': json.loads(r['payload'])['kind'],
                   'at': json.loads(r['payload'])['state']['updated_at'], 'details': json.loads(r['payload'])['details']} for r in receipts]
        return {**state, 'events': events}

    def fill(self, state, quote, action, units, now):
        quote.validate(now)
        config = state['config']
        if quote.ask-quote.bid > Decimal(config['max_spread']):
            raise ValueError('Gold spread exceeds this account limit')
        try:
            if not isinstance(units, str) or len(units) > 32:
                raise ValueError('Gold ounces must be a decimal string')
            quantity = Decimal(units)
        except InvalidOperation as error:
            raise ValueError('Invalid gold ounces') from error
        if not quantity.is_finite() or quantity <= 0 or quantity % Decimal('.01') or quantity > 10:
            raise ValueError('Use 0.01-ounce increments, maximum 10 ounces')
        cash, held, basis = map(Decimal, (state['cash'], state['quantity'], state['basis']))
        price = quote.ask+Decimal(config['slippage']) if action == 'BUY' else quote.bid-Decimal(config['slippage'])
        charge = quantity*Decimal(config['commission'])
        if price <= 0:
            raise ValueError('Invalid simulated gold fill price')
        if action == 'BUY':
            if quantity*price+charge > cash:
                raise ValueError('Insufficient simulated USD balance')
            cash -= quantity*price+charge
            held += quantity
            basis += quantity*price+charge
        elif action == 'SELL':
            if quantity > held:
                raise ValueError('Short selling is not enabled')
            allocated = basis*quantity/held
            cash += quantity*price-charge
            basis -= allocated
            held -= quantity
            state['realized'] = str(Decimal(state['realized'])+quantity*price-charge-allocated)
        else:
            raise ValueError('Unknown gold order side')
        state.update(cash=str(cash), quantity=str(held), basis=str(basis), fees=str(Decimal(state['fees'])+charge))
        if held == 0:
            state['basis'] = '0'
        state['phase'] = 'FILLED'
        equity = cash+held*quote.bid
        pnl = Decimal(state['realized'])+held*quote.bid-Decimal(state['basis'])
        if cash < 0 or held < 0 or abs(equity-Decimal(config['initial_cash'])-pnl) > Decimal('1e-20'):
            raise ArithmeticError('Gold accounting conservation failed')
        return {'side': action, 'ounces': str(quantity), 'price': str(price), 'fee': str(charge), 'quote_ts': quote.ts,
                'bid': str(quote.bid), 'ask': str(quote.ask), 'simulated': True, 'source': SOURCE}

    def command(self, identity, owner, body, now, quote=None):
        with localcontext() as precision, self.connect() as db:
            precision.prec = 50
            db.execute('BEGIN IMMEDIATE')
            state = self._read(db, identity, owner)
            receipt = db.execute('SELECT request_hash FROM events WHERE account_id=? AND key=?', (identity, body['request_id'])).fetchone()
            if receipt:
                if receipt['request_hash'] != digest(body):
                    raise Conflict('Request ID reused with a different command')
                return state
            if body['expected_revision'] != state['revision']:
                raise Conflict('Account changed; refresh before submitting again')
            if state['mode'] == 'POLICY':
                raise Conflict('Experimental policy accounts require the policy command endpoint')
            action = body['action']
            if action in ('PAUSE', 'RESUME'):
                state['status'] = 'PAUSED' if action == 'PAUSE' else 'ACTIVE'
                state['intent'] = None
                details = {'inventory_preserved': True}
            else:
                if state['status'] != 'ACTIVE' or state['mode'] != 'MANUAL':
                    raise Conflict('Manual orders require an active manual account')
                if quote is None:
                    raise ValueError('No current gold quote')
                details = self.fill(state, quote, action, body['ounces'], now)
            self._write(db, state, body['request_id'], digest(body), action, now, details)
            return state

    def advance(self, quote, now):
        """Server timer only. Fixed intent budget and one decision per closed bar."""
        quote.validate(now)
        market = self.market(now)
        signal = market['analysis']
        with localcontext() as precision, self.connect() as db:
            precision.prec = 50
            db.execute('BEGIN IMMEDIATE')
            ids = [r[0] for r in db.execute('SELECT id FROM accounts')]
            for identity in ids:
                state = self._read(db, identity)
                if state['mode'] != 'SMA20' or state['status'] != 'ACTIVE':
                    continue
                intent = state['intent']
                if intent and quote.ts > intent['observed_at']:
                    details = {'reason': 'expired intent; no historical fill'}
                    if quote.ts-intent['observed_at'] <= 60000 and now-intent['observed_at'] <= 60000:
                        try:
                            held = Decimal(state['quantity'])
                            units = held if intent['target'] == 'FLAT' else Decimal(intent['budget'])/(quote.ask+Decimal(state['config']['slippage'])+Decimal(state['config']['commission']))
                            units = min(units, Decimal(10)).quantize(Decimal('.01'), rounding=ROUND_FLOOR)
                            if units > 0:
                                details = self.fill(state, quote, 'SELL' if intent['target'] == 'FLAT' else 'BUY', str(units), now)
                            else:
                                details = {'reason': 'below research quantity step'}
                        except ValueError as error:
                            details = {'reason': str(error)}
                    state['intent'] = None
                    self._write(db, state, f"execute:{intent['bar']}", digest(intent), 'AUTO_RESULT', now, details)
                if signal['status'] != 'READY' or signal['available_at'] == state['last_bar'] or signal['available_at'] > quote.ts:
                    continue
                state['last_bar'] = signal['available_at']
                state['phase'] = 'OBSERVING'
                held = Decimal(state['quantity'])
                target = signal['target']
                if (target == 'LONG' and held == 0) or (target == 'FLAT' and held > 0):
                    state['intent'] = {'target': target, 'observed_at': now, 'bar': signal['available_at'],
                                       'budget': str(Decimal(state['cash'])*Decimal(state['config']['allocation']))}
                    state['phase'] = 'WAITING_NEXT_QUOTE'
                self._write(db, state, f"bar:{signal['available_at']}", digest(signal), 'DECISION', now, signal)
