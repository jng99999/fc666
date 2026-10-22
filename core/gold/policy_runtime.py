"""Opt-in, owner-scoped forward paper execution. No training or broker dependency.

The f64 contract is deliberately distinct from the research v1 float32 actor.
Registration and qualification are operator-owned local files, never HTTP input.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, localcontext
import hashlib
import json
import math
from pathlib import Path
from itertools import islice
import re
from threading import Lock
import time

from core.gold.history import Bar, HOUR
from core.gold.ledger import Conflict, canonical, digest
from core.gold.market import Quote, SOURCE

PROTOCOL = 'gold-policy-json-f64-v1'
V2_PROTOCOL = 'gold-policy-ablation-f64-v2'
FEATURE_VERSION = 'gold-hourly-observation-f64-v1'
FEATURES = ('return_1', 'return_4', 'return_20', 'range_20', 'volatility_20',
            'spread_fraction', 'elapsed_hours_log', 'cash_fraction', 'exposure', 'drawdown')
TARGETS = ('0', '.25', '.5')
MONEY = ('initial_cash', 'allocation', 'max_spread', 'slippage', 'commission')
MAX_AGE = 120000
MIN_LOGIT_MARGIN = 1e-6
MAX_RAW_SAMPLES = 100000


class Rejected(ValueError):
    """Stable machine-readable refusal code; never falls back to another policy."""


def money_config(config):
    result = {}
    for key in MONEY:
        value = Decimal(config[key])
        if not value.is_finite():
            raise Rejected('INVALID_ACCOUNT_CONFIG')
        result[key] = str(value.normalize())
    if not (0 < Decimal(result['initial_cash']) <= 1000000
            and Decimal(result['allocation']) == Decimal('.5')
            and 0 < Decimal(result['max_spread']) <= 10
            and all(0 <= Decimal(result[k]) <= 10 for k in ('slippage', 'commission'))):
        raise Rejected('INVALID_ACCOUNT_CONFIG')
    return result


def local_json(root, name, expected):
    if not isinstance(name, str) or Path(name).name != name or not name.endswith('.json'):
        raise Rejected('LOCAL_JSON_FILE_REQUIRED')
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise Rejected('LOCAL_JSON_FILE_REQUIRED')
    with path.open('rb') as handle:
        raw = handle.read(1000001)
    if len(raw) > 1000000 or hashlib.sha256(raw).hexdigest() != expected:
        raise Rejected('ARTIFACT_HASH_MISMATCH')
    return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(Rejected('NONFINITE_JSON')))


@dataclass(frozen=True)
class Model:
    identity: str
    actor_hash: str
    context_hash: str
    actor: dict
    config: dict
    protocol: str
    valid_until: int


class Registry:
    """Reload and hash-check on use so revocation also fences in-flight inference."""

    def __init__(self, directory=None, *, allow_fixtures=False):
        self.directory = Path(directory) if directory else None
        self.allow_fixtures = allow_fixtures

    def get(self, identity, now):
        if self.directory is None or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', identity):
            raise Rejected('MODEL_NOT_REGISTERED')
        try:
            path = self.directory / (identity+'.json')
            if path.is_symlink():
                raise Rejected('LOCAL_JSON_FILE_REQUIRED')
            with path.open('rb') as handle:
                raw = handle.read(32769)
            if len(raw) > 32768:
                raise Rejected('INVALID_MODEL_MANIFEST')
            manifest = json.loads(raw)
            if (manifest['protocol'] not in (PROTOCOL, V2_PROTOCOL) or manifest['feature_version'] != FEATURE_VERSION
                    or manifest['source'] != SOURCE):
                raise Rejected('UNSUPPORTED_MODEL_CONTRACT')
            if (manifest['approved_for_paper'] is not True
                    or type(manifest['valid_until']) is not int or now >= manifest['valid_until']):
                raise Rejected('MODEL_NOT_APPROVED_OR_EXPIRED')
            config = money_config(manifest['account_config'])
            context = {'protocol': manifest['protocol'], 'feature_version': FEATURE_VERSION,
                       'source': SOURCE, 'account_config': config}
            context_hash = digest(context)
            qualification = local_json(self.directory, manifest['qualification_file'], manifest['qualification_sha256'])
            if (qualification['eligible'] is not True or qualification['reasons'] != []
                    or qualification['actor_sha256'] != manifest['actor_sha256']
                    or qualification['context_hash'] != context_hash
                    or qualification['synthetic_fixture'] is not False and not self.allow_fixtures):
                raise Rejected('MODEL_NOT_QUALIFIED')
            actor = local_json(self.directory, manifest['actor_file'], manifest['actor_sha256'])
            validate_actor(actor)
            if actor['version'] != manifest['protocol']:
                raise Rejected('UNSUPPORTED_MODEL_CONTRACT')
            return Model(identity, manifest['actor_sha256'], context_hash, actor, config,
                         manifest['protocol'], manifest['valid_until'])
        except Rejected:
            raise
        except (OSError, KeyError, TypeError, ValueError, ArithmeticError, RecursionError) as error:
            raise Rejected('INVALID_MODEL_REGISTRATION') from error

    def available(self, now):
        """Operator directory only; expose metadata of currently verified models."""
        if self.directory is None or not self.directory.is_dir():
            return []
        entries = list(islice(self.directory.iterdir(), 513))
        if len(entries) > 512:
            raise Rejected('MODEL_REGISTRY_CAPACITY_EXCEEDED')
        result = []
        for path in entries:
            if path.suffix != '.json' or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', path.stem):
                continue
            try:
                model = self.get(path.stem, now)
            except Rejected:
                continue
            result.append({'id': model.identity, 'protocol': model.protocol, 'feature_version': FEATURE_VERSION,
                           'valid_until': model.valid_until, 'account_config': model.config})
        return sorted(result, key=lambda row: row['id'])


def validate_actor(actor):
    if (not isinstance(actor, dict) or actor.get('version') not in (PROTOCOL, V2_PROTOCOL) or actor.get('features') != list(FEATURES)
            or actor.get('actions') != list(TARGETS)):
        raise Rejected('UNSUPPORTED_MODEL_CONTRACT')
    if actor['version'] == V2_PROTOCOL:
        validate_preprocessor(actor)
    elif set(actor) != {'version', 'features', 'actions', 'layers'}:
        raise Rejected('UNSUPPORTED_MODEL_CONTRACT')
    width, parameters = len(FEATURES), 0
    layers = actor['layers']
    if not isinstance(layers, list) or not 1 <= len(layers) <= 8:
        raise Rejected('INVALID_ACTOR_DIMENSIONS')
    for layer in layers:
        if layer['kind'] == 'linear':
            if set(layer) != {'kind', 'weight', 'bias'}:
                raise Rejected('INVALID_ACTOR_OPERATION')
            weights, bias = layer['weight'], layer['bias']
            if not (isinstance(weights, list) and isinstance(bias, list)
                    and 1 <= len(weights) == len(bias) <= 128
                    and all(isinstance(row, list) and len(row) == width for row in weights)):
                raise Rejected('INVALID_ACTOR_DIMENSIONS')
            numbers = [v for row in weights for v in row]+bias
            if not all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1000000 for v in numbers):
                raise Rejected('INVALID_ACTOR_WEIGHTS')
            parameters += len(numbers)
            width = len(bias)
        elif layer != {'kind': 'tanh'}:
            raise Rejected('INVALID_ACTOR_OPERATION')
    if width != 3 or parameters > 50000:
        raise Rejected('INVALID_ACTOR_DIMENSIONS')


def validate_preprocessor(actor):
    """Explicit deployment v2, never a native f32 actor retag/default transform."""
    if (actor['input'] != 'raw_f64_observation' or actor['arm'] not in ('A', 'B', 'C')
            or actor['arithmetic'] != 'python_f64_fsum_tanh_margin_1e-6'
            or type(actor['min_logit_margin']) not in (float, int) or actor['min_logit_margin'] != MIN_LOGIT_MARGIN
            or not re.fullmatch('[0-9a-f]{64}', actor['native_actor_sha256'])
            or not re.fullmatch('[0-9a-f]{64}', actor['dataset'])
            or type(actor['train_end']) is not int or actor['train_end'] % HOUR
            or type(actor['seed']) is not int or type(actor['timesteps']) is not int or actor['timesteps'] <= 0
            or actor['weights_sha256'] != digest(actor['layers'])):
        raise Rejected('INVALID_V2_DEPLOYMENT_CONTRACT')
    scaler = actor['scaler']
    if actor['arm'] == 'A':
        if scaler is not None:
            raise Rejected('ARM_A_REQUIRES_NO_SCALER')
        return
    if (not isinstance(scaler, dict) or scaler['features'] != list(FEATURES[:7])
            or type(scaler['ddof']) is not int or scaler['ddof'] != 0
            or scaler['clip'] != [-10, 10] or scaler['fit'] != 'training_decision_observations_only'
            or not isinstance(scaler['mean'], list) or not isinstance(scaler['std'], list)
            or len(scaler['mean']) != 7 or len(scaler['std']) != 7
            or not all(type(v) in (int, float) and math.isfinite(v) for v in scaler['mean']+scaler['std'])
            or not all(v > 0 for v in scaler['std'])
            or any(type(scaler[k]) is not int for k in ('samples', 'first_observed_at', 'last_observed_at', 'train_end'))
            or not 2 <= scaler['samples'] <= 50000
            or not 0 <= scaler['first_observed_at'] < scaler['last_observed_at'] < scaler['train_end'] == actor['train_end']):
        raise Rejected('INVALID_FROZEN_SCALER')


def preprocess(actor, observation):
    """Raw finite f64 ten-vector -> exactly one frozen preprocessing pass."""
    if actor.get('version') not in (PROTOCOL, V2_PROTOCOL):
        raise Rejected('UNSUPPORTED_MODEL_CONTRACT')
    values = list(observation)
    if len(values) != len(FEATURES) or not all(type(v) in (int, float) and math.isfinite(v) for v in values):
        raise Rejected('INVALID_OBSERVATION')
    values = [max(-10., min(10., v)) for v in values]
    if actor['version'] == V2_PROTOCOL and actor['arm'] != 'A':
        scaler = actor['scaler']
        scaled = [(v-m)/s for v, m, s in zip(values[:7], scaler['mean'], scaler['std'])]
        if not all(math.isfinite(v) for v in scaled):
            raise Rejected('NONFINITE_SCALED_OBSERVATION')
        market = [max(-10., min(10., v)) for v in scaled]
        if actor['arm'] == 'C':
            market = [0.]*7
        values = market+values[7:]
    return values


def infer(actor, observation, deadline):
    """Bounded f64, row-order fsum, low-margin abstention. Per-row deadline."""
    values = preprocess(actor, observation)
    for layer in actor['layers']:
        if layer['kind'] == 'linear':
            output = []
            for weights, bias in zip(layer['weight'], layer['bias']):
                if time.monotonic() > deadline:
                    raise Rejected('INFERENCE_TIMEOUT')
                output.append(math.fsum(w*v for w, v in zip(weights, values))+bias)
            values = output
        else:
            values = [math.tanh(v) for v in values]
        if not all(math.isfinite(v) for v in values):
            raise Rejected('NONFINITE_INFERENCE')
    if len(values) != 3:
        raise Rejected('INVALID_INFERENCE_DIMENSIONS')
    ranked = sorted(range(3), key=values.__getitem__, reverse=True)
    if values[ranked[0]]-values[ranked[1]] <= MIN_LOGIT_MARGIN:
        raise Rejected('LOW_LOGIT_MARGIN')
    return ranked[0]


def hourly_snapshot(store, now):
    """Bounded observed history, including market closures; no calendar truncation."""
    with store.connect() as db:
        rows = db.execute('SELECT * FROM quotes WHERE ts<? ORDER BY ts DESC LIMIT ?',
                          (now//HOUR*HOUR, MAX_RAW_SAMPLES)).fetchall()
    groups = {}
    for row in reversed(rows):
        groups.setdefault(row['ts']//HOUR*HOUR, []).append(row)
    bars = []
    for start, group in sorted(groups.items()):
        gap = max((b['ts']-a['ts'] for a, b in zip(group, group[1:])), default=HOUR)
        if group[0]['ts']-start > 20000 or start+HOUR-group[-1]['ts'] > 20000 or gap > 30000:
            continue
        mids = [(Decimal(r['bid'])+Decimal(r['ask']))/2 for r in group]
        first, last = group[0], group[-1]
        bars.append(Bar.parse([start, first['ts'], last['ts'], first['bid'], first['ask'],
                               last['bid'], last['ask'], str(max(mids)), str(min(mids)), len(group), gap]))
    return {'source': SOURCE, 'bars': tuple(bars[-100:])}


class HourlyFeed:
    """Cache sealed hours; a late-but-fresh closing sample invalidates the prefix."""

    def __init__(self, store):
        self.store = store
        self.lock = Lock()
        self.hour = None
        self.snapshot = None

    def __call__(self, now):
        with self.lock:
            hour = now//HOUR*HOUR
            key = (hour, closed_watermark(self.store, hour))
            if self.hour != key:
                self.snapshot = hourly_snapshot(self.store, now)
                self.hour = key
            return self.snapshot


def closed_watermark(store, hour):
    """Indexed lookup only, no historical aggregation on an unchanged prefix."""
    with store.connect() as db:
        return db.execute('SELECT max(ts) FROM quotes WHERE ts<?', (hour,)).fetchone()[0]


def observe(snapshot, state, now):
    if snapshot['source'] != SOURCE:
        raise Rejected('FEATURE_SOURCE_MISMATCH')
    bars = snapshot['bars']
    if len(bars) < 100:
        raise Rejected('WAITING_HISTORY')
    # Reparse even provider-supplied bars; reject partial, duplicate or unordered bars.
    bars = [Bar.parse(bar.row()) for bar in bars]
    if any(b.start <= a.start for a, b in zip(bars, bars[1:])):
        raise Rejected('INVALID_HOURLY_SEQUENCE')
    bar = bars[-1]
    close_at = bar.start+HOUR
    if not 0 <= now-close_at <= MAX_AGE:
        raise Rejected('WAITING_FRESH_CLOSED_HOUR')
    if close_at < state.get('policy_not_before', state['created_at']//HOUR*HOUR+HOUR):
        raise Rejected('WAITING_FORWARD_HOUR')
    values, peak = observation_values(bars, state['cash'], state['quantity'],
                                     state.get('policy_peak', state['config']['initial_cash']))
    return close_at, values, peak, digest({'source': SOURCE, 'bars': [b.row() for b in bars]})


def observation_values(bars, cash, quantity, peak):
    """Pure f64 evaluator shared with offline research; no feed/source relabelling.

    Input bars must already be validated, increasing, closed 1h paired bars.
    The online observe() wrapper enforces source, closure, bounds and warmup.
    """
    with localcontext() as precision:
        precision.prec = 28
        return _observation_values(bars, cash, quantity, peak)


def _observation_values(bars, cash, quantity, peak):
    if len(bars) < 100:
        raise Rejected('WAITING_HISTORY')
    bar = bars[-1]
    close = float(bar.close)
    returns = [math.log(float(bars[j].close/bars[j-1].close)) for j in range(len(bars)-20, len(bars))]
    mean = math.fsum(returns)/20
    values = [math.log(close/float(bars[-1-n].close)) for n in (1, 4, 20)]
    values += [float((max(b.high for b in bars[-20:])-min(b.low for b in bars[-20:]))/bar.close),
               math.sqrt(math.fsum((r-mean)**2 for r in returns)/20),
               float((bar.ask_close-bar.bid_close)/bar.close), math.log1p((bar.start-bars[-2].start)/HOUR)]
    cash, quantity = Decimal(cash), Decimal(quantity)
    equity = cash+quantity*bar.bid_close
    peak = max(Decimal(peak), equity)
    if equity <= 0:
        raise Rejected('INVALID_ACCOUNT_EQUITY')
    values += [float(cash/equity), float(quantity*bar.bid_close/equity),
               float((peak-equity)/peak)]
    if not all(math.isfinite(v) for v in values):
        raise Rejected('NONFINITE_OBSERVATION')
    values = [max(-10., min(10., v)) for v in values]
    return values, str(peak)


class PolicyRuntime:
    def __init__(self, store, registry, clock, *, enabled=False, bars_provider=None, inference_ms=100):
        self.store, self.registry, self.clock = store, registry, clock
        self.enabled = enabled
        self.bars_provider = bars_provider or HourlyFeed(store)
        self.inference_ms = inference_ms

    def require_enabled(self):
        if not self.enabled:
            raise Rejected('POLICY_RUNTIME_DISABLED')

    def status(self):
        now = self.clock()
        snapshot = self.bars_provider(now)
        if snapshot['source'] != SOURCE:
            raise Rejected('FEATURE_SOURCE_MISMATCH')
        count = len(snapshot['bars'])
        latest = snapshot['bars'][-1].start+HOUR if count else None
        warmup = ('WAITING_HISTORY' if count < 100 else
                  ('READY' if 0 <= now-latest <= MAX_AGE else 'WAITING_FRESH_CLOSED_HOUR'))
        models = self.registry.available(now)
        status = 'DISABLED' if not self.enabled else ('NO_QUALIFIED_MODELS' if not models else warmup)
        return {'enabled': self.enabled, 'trading_enabled': False, 'source': SOURCE,
                'required_bars': 100, 'complete_bars': count, 'latest_close_at': latest,
                'warmup_status': warmup, 'status': status, 'models': models, 'checked_at': now}

    def request_receipt(self, owner, request_id):
        """Historical recovery does not require a live model or an enabled runtime.

        A missing receipt is only a snapshot observation: another transaction may
        still be in flight. UUIDs are account-scoped in the existing ledger, so
        ambiguous cross-account matches must never be resolved arbitrarily.
        """
        with self.store.connect() as db:
            db.execute('BEGIN')
            rows = db.execute('''SELECT e.account_id,e.seq,e.request_hash,e.payload,e.hash
                                 FROM events e JOIN accounts a ON a.id=e.account_id
                                 WHERE a.owner=? AND e.key=?''', (owner, request_id)).fetchall()
            matches = []
            for row in rows:
                account = self.store._read(db, row['account_id'], owner)
                if account['mode'] != 'POLICY':
                    continue
                payload = json.loads(row['payload'])
                state = payload['state']
                if (digest(payload) != row['hash'] or state['id'] != row['account_id']
                        or state['revision'] != row['seq'] or state['mode'] != 'POLICY'):
                    raise ValueError('Gold policy receipt integrity failed')
                kind = payload['kind']
                if kind not in ('CREATED', 'POLICY_DECIDE', 'POLICY_PAUSE', 'POLICY_RESUME', 'POLICY_STOP'):
                    raise ValueError('Gold policy receipt kind integrity failed')
                if kind == 'CREATED' and row['request_hash'] != digest(state['config']):
                    raise ValueError('Gold policy creation receipt integrity failed')
                if kind != 'CREATED' and row['request_hash'] != digest({
                        'request_id': request_id, 'expected_revision': row['seq']-1, 'action': kind[7:]}):
                    raise ValueError('Gold policy command receipt integrity failed')
                matches.append({'found': True, 'request_id': request_id, 'account_id': row['account_id'],
                                'seq': row['seq'], 'kind': kind, 'event_hash': row['hash'],
                                'request_hash': row['request_hash'],
                                'hash_scope': 'ACCOUNT_CONFIG' if kind == 'CREATED' else 'COMMAND_BODY',
                                'state': state, 'details': payload['details'], 'account': account})
            if len(matches) > 1:
                raise Conflict('Request ID matches multiple policy accounts; inspect account histories')
            if matches:
                return matches[0]
            return {'found': False, 'request_id': request_id, 'absence_proves_not_applied': False}

    def create(self, owner, config, model_id, request_id, experimental):
        self.require_enabled()
        if experimental is not True:
            raise Rejected('EXPLICIT_EXPERIMENT_REQUIRED')
        now = self.clock()
        model = self.registry.get(model_id, now)
        if money_config(config) != model.config:
            raise Rejected('MODEL_ACCOUNT_CONTEXT_MISMATCH')
        # The create request must hash identically on retry, even across an hour.
        with self.store.connect() as db:
            rows = db.execute('SELECT account_id FROM events WHERE key=?', (request_id,)).fetchall()
            for row in rows:
                try:
                    previous = self.store._read(db, row['account_id'], owner)
                except KeyError:
                    continue
                policy = previous['config'].get('policy', {})
                if (previous['mode'] != 'POLICY' or money_config(previous['config']) != model.config
                        or policy.get('model_id') != model_id or policy.get('actor_sha256') != model.actor_hash
                        or policy.get('context_hash') != model.context_hash):
                    raise Conflict('Create request ID reused with different policy parameters')
                return previous
        policy = {'model_id': model_id, 'actor_sha256': model.actor_hash, 'context_hash': model.context_hash,
                  'protocol': model.protocol, 'feature_version': FEATURE_VERSION}
        return self.store.create(owner, {**model.config, 'mode': 'POLICY', 'policy': policy}, request_id, now)

    def _model(self, state, now):
        self.require_enabled()
        policy = state['config']['policy']
        model = self.registry.get(policy['model_id'], now)
        if (model.actor_hash != policy['actor_sha256'] or model.context_hash != policy['context_hash']
                or model.protocol != policy['protocol']
                or model.config != money_config(state['config'])):
            raise Rejected('MODEL_ACCOUNT_CONTEXT_MISMATCH')
        return model

    def command(self, identity, owner, body):
        """DECIDE inference is outside the DB transaction; commit fences every control."""
        now = self.clock()
        with self.store.connect() as db:
            state = self.store._read(db, identity, owner)
            if state['mode'] != 'POLICY':
                raise Conflict('Policy commands require an experimental policy account')
            old = db.execute('SELECT request_hash FROM events WHERE account_id=? AND key=?',
                             (identity, body['request_id'])).fetchone()
            if old:
                if old['request_hash'] != digest(body):
                    raise Conflict('Request ID reused with a different command')
                return state
        if body['expected_revision'] != state['revision']:
            raise Conflict('Account changed; refresh before submitting again')
        action = body['action']
        if action not in ('DECIDE', 'PAUSE', 'RESUME', 'STOP'):
            raise Rejected('INVALID_POLICY_COMMAND')
        generation = state.get('control_generation', 0)
        details = {'model_id': state['config']['policy']['model_id'],
                   'actor_sha256': state['config']['policy']['actor_sha256'],
                   'feature_version': FEATURE_VERSION, 'context_hash': state['config']['policy']['context_hash'],
                   'protocol': state['config']['policy']['protocol'],
                   'observation_hash': None, 'market_hash': None, 'reason': None, 'simulated': True}
        close_at, target, peak = None, None, None
        if action == 'DECIDE':
            if state['status'] != 'ACTIVE':
                raise Conflict('Policy decisions require an active account')
            try:
                model = self._model(state, now)
                close_at, observation, peak, market_hash = observe(self.bars_provider(now), state, now)
                if state['last_bar'] is not None and close_at <= state['last_bar']:
                    raise Conflict('Closed hour already decided; historical decisions are never replayed')
                details.update(close_at=close_at, observation_hash=digest(observation), market_hash=market_hash,
                               processed_observation_hash=digest(preprocess(model.actor, observation)))
                deadline = time.monotonic()+self.inference_ms/1000
                target_index = infer(model.actor, observation, deadline)
                if time.monotonic() > deadline:
                    raise Rejected('INFERENCE_TIMEOUT')
                if type(target_index) is not int or not 0 <= target_index < 3:
                    raise Rejected('INVALID_INFERENCE_ACTION')
                target = TARGETS[target_index]
                details['target_weight'] = target
            except Conflict:
                raise
            except Exception as error:
                details['reason'] = str(error) if isinstance(error, Rejected) else 'INFERENCE_OR_FEATURE_ERROR'
        with localcontext() as precision, self.store.connect() as db:
            precision.prec = 50
            db.execute('BEGIN IMMEDIATE')
            current = self.store._read(db, identity, owner)
            old = db.execute('SELECT request_hash FROM events WHERE account_id=? AND key=?',
                             (identity, body['request_id'])).fetchone()
            if old:
                if old['request_hash'] != digest(body):
                    raise Conflict('Request ID reused with a different command')
                return current
            if current['revision'] != state['revision'] or current.get('control_generation', 0) != generation:
                raise Conflict('Account changed during prediction; stale prediction rejected')
            committed_at = self.clock()
            if action != 'DECIDE':
                if current['status'] == 'STOPPED':
                    raise Conflict('Stopped policy accounts cannot be restarted')
                if action == 'RESUME':
                    self._model(current, committed_at)
                    current['policy_not_before'] = committed_at//HOUR*HOUR+HOUR
                current['status'] = {'PAUSE': 'PAUSED', 'RESUME': 'ACTIVE', 'STOP': 'STOPPED'}[action]
                current['control_generation'] = generation+1
                current['intent'] = None
                details['inventory_preserved'] = True
            else:
                if current['status'] != 'ACTIVE':
                    raise Conflict('Policy is no longer active')
                if close_at is not None:
                    current['last_bar'] = close_at
                if peak is not None:
                    current['policy_peak'] = peak
                if target is not None and details['reason'] is None:
                    try:
                        checked_model = self._model(current, committed_at)
                        committed_at = self.clock()
                        if committed_at >= checked_model.valid_until:
                            raise Rejected('MODEL_NOT_APPROVED_OR_EXPIRED')
                        if not 0 <= committed_at-close_at <= MAX_AGE:
                            raise Rejected('EXPIRED_DECISION')
                        row = db.execute('SELECT * FROM quotes ORDER BY ts DESC LIMIT 1').fetchone()
                        if row is None:
                            raise Rejected('MISSING_EXECUTION_QUOTE')
                        quote = Quote(row['ts'], Decimal(row['bid']), Decimal(row['ask']))
                        quote.validate(committed_at)
                        if quote.ts < close_at:
                            raise Rejected('QUOTE_BEFORE_DECISION')
                        if quote.ask-quote.bid > Decimal(current['config']['max_spread']):
                            raise Rejected('EXCESSIVE_SPREAD')
                        held, cash = Decimal(current['quantity']), Decimal(current['cash'])
                        unit_cost = quote.ask+Decimal(current['config']['slippage'])+Decimal(current['config']['commission'])
                        desired = ((cash+held*quote.bid)*Decimal(target)/unit_cost).quantize(Decimal('.01'), rounding=ROUND_FLOOR)
                        delta = desired-held
                        units = min(abs(delta), Decimal(10))
                        if delta > 0:
                            units = min(units, (cash/unit_cost).quantize(Decimal('.01'), rounding=ROUND_FLOOR))
                        if units > 0:
                            details['fill'] = self.store.fill(current, quote, 'BUY' if delta > 0 else 'SELL', str(units), committed_at)
                        else:
                            details['reason'] = 'TARGET_WITHIN_QUANTITY_STEP'
                    except (ValueError, ArithmeticError) as error:
                        # fill() may mutate before an invariant error. Roll back rather than persist corruption.
                        if isinstance(error, ArithmeticError):
                            raise
                        details['reason'] = str(error)
                current['phase'] = 'FILLED' if 'fill' in details else ('WAITING' if close_at is None else 'REJECTED')
            self.store._write(db, current, body['request_id'], digest(body), 'POLICY_'+action, committed_at, details)
            return current

    def advance(self):
        """Current hour only; WAITING may retry a newly observed closing prefix."""
        if not self.enabled:
            return
        now = self.clock()
        hour = now//HOUR*HOUR
        key = f'policy-hour:{hour}:{closed_watermark(self.store, hour)}'
        with self.store.connect() as db:
            candidates = []
            for row in db.execute('SELECT id,owner FROM accounts').fetchall():
                state = self.store._read(db, row['id'], row['owner'])
                if state['mode'] != 'POLICY' or state['status'] != 'ACTIVE':
                    continue
                if state['last_bar'] is not None and state['last_bar'] >= now//HOUR*HOUR:
                    continue
                if not db.execute('SELECT 1 FROM events WHERE account_id=? AND key=?', (row['id'], key)).fetchone():
                    candidates.append((row['id'], row['owner'], state['revision']))
        for identity, owner, revision in candidates:
            try:
                self.command(identity, owner, {'request_id': key, 'expected_revision': revision, 'action': 'DECIDE'})
            except Conflict:
                # A user control or a second server worker won the transaction.
                continue
