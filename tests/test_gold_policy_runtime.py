"""Synthetic execution fixtures only. These tests are not profitability evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from apps.api.gold_service import create_app
from core.gold.history import Bar, HOUR
from core.gold.ledger import Conflict, Store, canonical, digest
from core.gold.market import Quote, SOURCE
from core.gold import policy_runtime as runtime
from core.gold.policy_runtime import (FEATURES, FEATURE_VERSION, PROTOCOL, TARGETS,
                                     PolicyRuntime, Registry, Rejected, hourly_snapshot, infer, observe)

NOW = 1792500000000//HOUR*HOUR
CONFIG = {'initial_cash': '10000', 'allocation': '.5', 'max_spread': '2',
          'slippage': '.05', 'commission': '.10'}


def write_json(path, value):
    raw = canonical(value).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def register(directory, *, eligible=True, version=PROTOCOL, synthetic=True):
    actor = {'version': version, 'features': list(FEATURES), 'actions': list(TARGETS),
             'layers': [{'kind': 'linear', 'weight': [[0.]*10 for _ in range(3)], 'bias': [0., 1., 0.]}]}
    actor_hash = write_json(directory/'actor.json', actor)
    config = runtime.money_config(CONFIG)
    context_hash = digest({'protocol': PROTOCOL, 'feature_version': FEATURE_VERSION,
                           'source': SOURCE, 'account_config': config})
    evidence = {'eligible': eligible, 'reasons': [] if eligible else ['fixed action, not admitted'],
                'actor_sha256': actor_hash, 'context_hash': context_hash, 'synthetic_fixture': synthetic}
    evidence_hash = write_json(directory/'qualification.json', evidence)
    manifest = {'protocol': PROTOCOL, 'feature_version': FEATURE_VERSION, 'source': SOURCE,
                'approved_for_paper': True, 'valid_until': NOW+100*HOUR, 'account_config': CONFIG,
                'actor_file': 'actor.json', 'actor_sha256': actor_hash,
                'qualification_file': 'qualification.json', 'qualification_sha256': evidence_hash}
    write_json(directory/'fixture.json', manifest)
    return manifest


def bars(count=100, end=NOW):
    result = []
    for i in range(count):
        start = end-(count-i)*HOUR
        price = Decimal(2000)+Decimal(i)/10
        result.append(Bar(start, start+1000, start+HOUR-1000, price, price+1, price, price+1,
                          price+2, price-1, 360, 10000))
    return result


@pytest.fixture
def rig(tmp_path):
    root = tmp_path/'registry'
    root.mkdir()
    register(root)
    store = Store(tmp_path/'paper.db')
    clock = [NOW-1000]
    engine = PolicyRuntime(store, Registry(root, allow_fixtures=True), lambda: clock[0], enabled=True,
                           bars_provider=lambda now: {'source': SOURCE, 'bars': bars()})
    account = engine.create('alice', CONFIG, 'fixture', str(uuid4()), True)
    clock[0] = NOW+1000
    store.put_quote(Quote(clock[0], Decimal(2100), Decimal('2100.5')), clock[0])
    return engine, account, clock, root


def command(engine, account, action='DECIDE', owner='alice', key=None):
    return engine.command(account['id'], owner, {'request_id': key or str(uuid4()),
                          'expected_revision': account['revision'], 'action': action})


def read(engine, account):
    return engine.store.read(account['id'], 'alice')


def test_full_create_predict_fill_and_ledger_query(rig):
    engine, account, _, _ = rig
    result = command(engine, account)
    assert Decimal(result['quantity']) == Decimal('1.19')
    receipt = read(engine, account)['events'][0]
    assert receipt['kind'] == 'POLICY_DECIDE'
    details = receipt['details']
    assert details['reason'] is None and details['target_weight'] == '.25'
    assert details['fill']['simulated'] is True and details['fill']['quote_ts'] >= details['close_at']
    for key in ('actor_sha256', 'context_hash', 'observation_hash', 'market_hash'):
        assert len(details[key]) == 64
    value = Decimal(result['cash'])+Decimal(result['quantity'])*Decimal(2100)
    assert value-Decimal(CONFIG['initial_cash']) == Decimal(result['realized'])+Decimal(result['quantity'])*Decimal(2100)-Decimal(result['basis'])


def test_same_command_concurrent_exactly_one_order_and_no_hour_replay(rig):
    engine, account, _, _ = rig
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: command(engine, account, key=key), range(4)))
    assert all(r['revision'] == 2 for r in results)
    assert len(read(engine, account)['events']) == 2
    with pytest.raises(Conflict, match='already decided'):
        command(engine, results[0])
    with pytest.raises(Conflict, match='different command'):
        command(engine, results[0], action='PAUSE', key=key)


def test_concurrent_distinct_commands_one_wins(rig):
    engine, account, _, _ = rig
    def attempt(_):
        try:
            return command(engine, account)['revision']
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert results.count(2) == 1 and results.count('conflict') == 3
    assert len(read(engine, account)['events']) == 2


@pytest.mark.parametrize('control', ['PAUSE', 'STOP', 'PAUSE_RESUME'])
def test_inflight_prediction_rejected_after_control_and_no_transaction_held(rig, monkeypatch, control):
    engine, account, _, _ = rig
    started, release = Event(), Event()
    def slow(actor, observation, deadline):
        started.set()
        assert release.wait(3)
        return 1
    monkeypatch.setattr(runtime, 'infer', slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(command, engine, account)
        assert started.wait(3)
        controlled = command(engine, account, action='PAUSE' if control == 'PAUSE_RESUME' else control)
        if control == 'PAUSE_RESUME':
            controlled = command(engine, controlled, action='RESUME')
        release.set()
        with pytest.raises(Conflict, match='changed during prediction'):
            future.result(timeout=3)
    assert controlled['status'] == {'PAUSE': 'PAUSED', 'STOP': 'STOPPED', 'PAUSE_RESUME': 'ACTIVE'}[control]
    assert read(engine, account)['quantity'] == '0'
    assert read(engine, account)['control_generation'] == (2 if control == 'PAUSE_RESUME' else 1)


def test_stop_survives_restart_and_cannot_resume(rig):
    engine, account, clock, root = rig
    stopped = command(engine, account, action='STOP')
    reopened = PolicyRuntime(Store(engine.store.path), Registry(root, allow_fixtures=True), lambda: clock[0], enabled=True)
    reopened.advance()
    assert read(reopened, account)['revision'] == stopped['revision']
    for action in ('RESUME', 'DECIDE'):
        with pytest.raises(Conflict):
            command(reopened, stopped, action=action)


def test_pause_resume_fences_pre_pause_hour_and_preserves_inventory(rig):
    engine, account, clock, _ = rig
    bought = command(engine, account)
    paused = command(engine, bought, action='PAUSE')
    with pytest.raises(Conflict):
        command(engine, paused)
    resumed = command(engine, paused, action='RESUME')
    assert resumed['quantity'] == bought['quantity'] and resumed['cash'] == bought['cash']
    assert resumed['control_generation'] == 2 and resumed['policy_not_before'] == NOW+HOUR
    clock[0] += 1
    result = command(engine, resumed)
    assert result['quantity'] == bought['quantity']
    assert read(engine, account)['events'][0]['details']['reason'] == 'WAITING_FORWARD_HOUR'


@pytest.mark.parametrize('kind', ['missing', 'stale', 'spread', 'before'])
def test_execution_quote_failure_no_fill_receipt(rig, kind):
    engine, account, clock, _ = rig
    with engine.store.connect() as db:
        db.execute('DELETE FROM quotes')
    if kind != 'missing':
        stamp = NOW-60000 if kind == 'stale' else (NOW-1 if kind == 'before' else clock[0])
        ask = Decimal(2110 if kind == 'spread' else '2100.5')
        with engine.store.connect() as db:
            db.execute('INSERT INTO quotes VALUES(?,?,?)', (stamp, '2100', str(ask)))
    result = command(engine, account)
    assert result['quantity'] == '0' and Decimal(result['cash']) == Decimal(CONFIG['initial_cash'])
    receipt = read(engine, account)['events'][0]['details']
    assert receipt['reason'] and receipt['observation_hash'] and 'fill' not in receipt
    assert result['last_bar'] == NOW


@pytest.mark.parametrize('failure', ['nan', 'wrong_dimension', 'exception', 'timeout'])
def test_inference_failures_closed_no_fallback(rig, monkeypatch, failure):
    engine, account, _, _ = rig
    if failure == 'timeout':
        engine.inference_ms = -1
    else:
        def bad(*_):
            if failure == 'exception':
                raise RuntimeError('private detail never returned')
            return float('nan') if failure == 'nan' else [1, 2]
        monkeypatch.setattr(runtime, 'infer', bad)
    result = command(engine, account)
    assert result['quantity'] == '0' and result['last_bar'] == NOW
    assert read(engine, account)['events'][0]['details']['reason'] in (
        'INVALID_INFERENCE_ACTION', 'INFERENCE_TIMEOUT', 'INFERENCE_OR_FEATURE_ERROR')
    with pytest.raises(Conflict):
        command(engine, result)


@pytest.mark.parametrize('kind', ['history', 'source', 'partial', 'future', 'stale', 'unordered'])
def test_features_fail_closed(rig, kind):
    engine, account, _, _ = rig
    series = bars(99 if kind == 'history' else 100, end=NOW+(HOUR if kind == 'future' else (-HOUR if kind == 'stale' else 0)))
    if kind == 'partial':
        series[-1] = replace(series[-1], last=series[-1].start+1000)
    if kind == 'unordered':
        series[0], series[1] = series[1], series[0]
    engine.bars_provider = lambda now: {'source': 'HistData' if kind == 'source' else SOURCE, 'bars': series}
    result = command(engine, account)
    assert result['quantity'] == '0' and result['phase'] == 'WAITING'
    assert read(engine, account)['events'][0]['details']['reason']


def test_default_disabled_explicit_experiment_context_and_unqualified_model(rig):
    engine, _, _, root = rig
    engine.enabled = False
    with pytest.raises(Rejected, match='DISABLED'):
        engine.create('bob', CONFIG, 'fixture', str(uuid4()), True)
    engine.enabled = True
    with pytest.raises(Rejected, match='EXPLICIT'):
        engine.create('bob', CONFIG, 'fixture', str(uuid4()), False)
    with pytest.raises(Rejected, match='CONTEXT'):
        engine.create('bob', {**CONFIG, 'commission': '.2'}, 'fixture', str(uuid4()), True)
    register(root, eligible=False)
    with pytest.raises(Rejected, match='NOT_QUALIFIED'):
        engine.create('bob', CONFIG, 'fixture', str(uuid4()), True)


def test_v1_is_not_runtime_contract_and_fixture_not_production_admission(rig):
    engine, _, clock, root = rig
    with pytest.raises(Rejected, match='NOT_QUALIFIED'):
        Registry(root).get('fixture', clock[0])
    register(root, version='gold-ppo-target-v1')
    with pytest.raises(Rejected, match='UNSUPPORTED_MODEL_CONTRACT'):
        engine.registry.get('fixture', clock[0])


@pytest.mark.parametrize('tamper', ['hash', 'expiry', 'gate', 'dimensions', 'weights', 'path', 'symlink'])
def test_registration_rechecked_before_each_order(rig, tamper):
    engine, account, clock, root = rig
    manifest = json.loads((root/'fixture.json').read_text())
    if tamper == 'hash':
        (root/'actor.json').write_text('{}')
    elif tamper == 'expiry':
        manifest['valid_until'] = clock[0]
    elif tamper == 'gate':
        qualification = json.loads((root/'qualification.json').read_text())
        qualification['eligible'] = False
        manifest['qualification_sha256'] = write_json(root/'qualification.json', qualification)
    elif tamper in ('dimensions', 'weights'):
        actor = json.loads((root/'actor.json').read_text())
        actor['layers'][0]['weight'][0] = [0.] if tamper == 'dimensions' else [float('nan')]*10
        manifest['actor_sha256'] = write_json(root/'actor.json', actor)
        qualification = json.loads((root/'qualification.json').read_text())
        qualification['actor_sha256'] = manifest['actor_sha256']
        manifest['qualification_sha256'] = write_json(root/'qualification.json', qualification)
    elif tamper == 'path':
        manifest['actor_file'] = '../actor.json'
    else:
        (root/'link.json').symlink_to(root/'actor.json')
        manifest['actor_file'] = 'link.json'
    write_json(root/'fixture.json', manifest)
    result = command(engine, account)
    assert result['quantity'] == '0'
    assert read(engine, account)['events'][0]['details']['reason']


def test_registration_revoked_during_inference(rig, monkeypatch):
    engine, account, _, root = rig
    def revoked(*_):
        manifest = json.loads((root/'fixture.json').read_text())
        manifest['approved_for_paper'] = False
        write_json(root/'fixture.json', manifest)
        return 1
    monkeypatch.setattr(runtime, 'infer', revoked)
    result = command(engine, account)
    assert result['quantity'] == '0'
    assert read(engine, account)['events'][0]['details']['reason'] == 'MODEL_NOT_APPROVED_OR_EXPIRED'


def test_owner_legacy_command_isolation_and_old_ledger_unchanged(rig):
    engine, account, clock, _ = rig
    with pytest.raises(KeyError):
        command(engine, account, owner='bob')
    legacy = engine.store.create('alice', {**CONFIG, 'mode': 'MANUAL'}, str(uuid4()), clock[0])
    before = engine.store.read(legacy['id'], 'alice')
    with pytest.raises(Conflict):
        engine.store.command(account['id'], 'alice', {'action': 'RESUME', 'request_id': str(uuid4()),
                             'expected_revision': 1}, clock[0])
    with pytest.raises(Conflict):
        command(engine, legacy)
    command(engine, account)
    assert engine.store.read(legacy['id'], 'alice') == before


def test_forward_only_creation_retries_and_server_restart_no_replay(rig):
    engine, account, clock, root = rig
    key = str(uuid4())
    created = engine.create('alice', CONFIG, 'fixture', key, True)
    clock[0] += HOUR
    assert engine.create('alice', CONFIG, 'fixture', key, True) == created
    clock[0] -= HOUR
    # Account created after the close cannot trade that prior hour.
    assert command(engine, created)['quantity'] == '0'
    engine.advance()
    before = read(engine, account)
    reopened = PolicyRuntime(Store(engine.store.path), Registry(root, allow_fixtures=True), lambda: clock[0],
                             enabled=True, bars_provider=engine.bars_provider)
    reopened.advance()
    assert read(reopened, account) == before


def test_real_hour_aggregation_requires_coverage_not_one_minute_or_future_fill(rig):
    engine, _, _, _ = rig
    with engine.store.connect() as db:
        rows = [(NOW-HOUR+i*10000, '2000', '2001') for i in range(360)]
        db.executemany('INSERT OR IGNORE INTO quotes VALUES(?,?,?)', rows)
    snapshot = hourly_snapshot(engine.store, NOW)
    assert len(snapshot['bars']) == 1 and snapshot['bars'][0].samples == 360
    assert hourly_snapshot(engine.store, NOW-1)['bars'] == ()
    with engine.store.connect() as db:
        db.execute('DELETE FROM quotes WHERE ts=?', (NOW-HOUR+100000,))
        db.execute('DELETE FROM quotes WHERE ts>=? AND ts<=?', (NOW-HOUR+200000, NOW-HOUR+240000))
    assert hourly_snapshot(engine.store, NOW)['bars'] == ()


def test_f64_known_logits_and_causal_observation(rig):
    engine, account, clock, root = rig
    actor = engine.registry.get('fixture', clock[0]).actor
    assert infer(actor, [0.]*10, runtime.time.monotonic()+1) == 1
    snapshot = {'source': SOURCE, 'bars': bars()}
    first = observe(snapshot, account, clock[0])
    assert len(first[1]) == 10 and first == observe(snapshot, account, clock[0])
    # A future suffix is refused, never used to fill or normalize a prior observation.
    with pytest.raises(Rejected, match='FRESH_CLOSED_HOUR'):
        observe({'source': SOURCE, 'bars': bars(end=NOW+HOUR)}, account, clock[0])


def test_api_end_to_end_default_off_no_upload_and_owner_boundary(tmp_path):
    root = tmp_path/'registry'
    root.mkdir()
    register(root)
    now = [NOW-1000]
    app = create_app(tmp_path/'api.db', background=False, secure=True, clock=lambda: now[0],
                     policy_registry_path=root, policy_enabled=True)
    # Explicit test harness override; the HTTP service never accepts fixture admission.
    app.state.policy.registry.allow_fixtures = True
    app.state.policy.bars_provider = lambda current: {'source': SOURCE, 'bars': bars()}
    payload = {**CONFIG, 'mode': 'POLICY', 'model_id': 'fixture', 'experimental': True, 'request_id': str(uuid4())}
    with TestClient(app, base_url='https://testserver') as alice, TestClient(app, base_url='https://testserver') as bob:
        created = alice.post('/api/v1/gold/policy/accounts', json=payload)
        assert created.status_code == 200
        account = created.json()
        now[0] = NOW+1000
        app.state.store.put_quote(Quote(now[0], Decimal(2100), Decimal('2100.5')), now[0])
        body = {'request_id': str(uuid4()), 'expected_revision': 1, 'action': 'DECIDE'}
        url = f"/api/v1/gold/policy/accounts/{account['id']}/command"
        assert bob.post(url, json=body).status_code == 401
        bob.get('/api/v1/gold/accounts')
        assert bob.post(url, json=body).status_code == 404
        filled = alice.post(url, json=body)
        assert filled.status_code == 200 and Decimal(filled.json()['quantity']) > 0
        assert alice.post(url, json=body).json() == filled.json()
        assert alice.get('/api/v1/gold/accounts').json()['accounts'] == []
        assert len(alice.get('/api/v1/gold/policy/accounts').json()['accounts']) == 1
        assert alice.get(f"/api/v1/gold/accounts/{account['id']}").status_code == 404
        events = alice.get(f"/api/v1/gold/policy/accounts/{account['id']}").json()['events']
        assert len(events) == 2 and events[0]['details']['fill']['simulated'] is True
        assert alice.post(url, json={**body, 'actor': {}}).status_code == 422
        assert alice.post('/api/v1/gold/accounts', json=payload).status_code == 422
    off = create_app(tmp_path/'off.db', background=False, clock=lambda: now[0], policy_enabled=False)
    with TestClient(off, base_url='https://testserver') as client:
        response = client.post('/api/v1/gold/policy/accounts', json=payload)
        assert response.status_code == 422 and response.json()['detail'] == 'POLICY_RUNTIME_DISABLED'


def test_live_startup_remains_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv('LIVE_TRADING', 'true')
    with pytest.raises(ValueError, match='only supports LIVE_TRADING=false'):
        create_app(tmp_path/'never.db', background=False, policy_enabled=True)
    assert not (tmp_path/'never.db').exists()


def test_target_flat_sells_only_inventory_and_max_ten_ounces(rig, monkeypatch):
    engine, account, clock, _ = rig
    clock[0] += 1
    engine.store.put_quote(Quote(clock[0], Decimal('2'), Decimal('2.5')), clock[0])
    capped = command(engine, account)
    assert Decimal(capped['quantity']) == 10
    assert read(engine, account)['events'][0]['details']['fill']['ounces'] == '10'
    clock[0] += HOUR
    engine.bars_provider = lambda now: {'source': SOURCE, 'bars': bars(end=NOW+HOUR)}
    engine.store.put_quote(Quote(clock[0], Decimal('2'), Decimal('2.5')), clock[0])
    monkeypatch.setattr(runtime, 'infer', lambda *_: 0)
    sold = command(engine, capped)
    assert Decimal(sold['quantity']) == 0 and Decimal(sold['cash']) >= 0
    assert read(engine, account)['events'][0]['details']['fill']['side'] == 'SELL'
    clock[0] += HOUR
    engine.bars_provider = lambda now: {'source': SOURCE, 'bars': bars(end=NOW+2*HOUR)}
    engine.store.put_quote(Quote(clock[0], Decimal('2'), Decimal('2.5')), clock[0])
    flat = command(engine, sold)
    assert Decimal(flat['quantity']) == 0
    assert read(engine, account)['events'][0]['details']['reason'] == 'TARGET_WITHIN_QUANTITY_STEP'


def test_restart_does_not_backfill_missed_hours(rig):
    engine, account, clock, _ = rig
    clock[0] += 5*HOUR
    # Even a fresh execution quote cannot revive an expired hourly observation.
    engine.store.put_quote(Quote(clock[0], Decimal(2100), Decimal('2100.5')), clock[0])
    engine.advance()
    result = read(engine, account)
    assert result['quantity'] == '0' and result['last_bar'] is None
    assert result['events'][0]['details']['reason'] == 'WAITING_FRESH_CLOSED_HOUR'
    engine.advance()
    assert read(engine, account)['revision'] == result['revision']


def test_model_disabled_after_creation_stops_decisions_but_stop_still_works(rig):
    engine, account, _, _ = rig
    engine.enabled = False
    result = command(engine, account)
    assert result['quantity'] == '0'
    assert read(engine, account)['events'][0]['details']['reason'] == 'POLICY_RUNTIME_DISABLED'
    assert command(engine, result, action='STOP')['status'] == 'STOPPED'


@pytest.mark.parametrize('bias', [[0., 0., 0.], [1., 1.+1e-8, 0.], [100., 100.+1e-7, 0.]])
def test_low_margin_and_tied_logits_abstain(bias, rig):
    engine, _, clock, _ = rig
    actor = engine.registry.get('fixture', clock[0]).actor
    actor['layers'][0]['bias'] = bias
    with pytest.raises(Rejected, match='LOW_LOGIT_MARGIN'):
        infer(actor, [0.]*10, runtime.time.monotonic()+1)


def test_feature_precision_and_account_ratios_not_ambient_context(rig):
    from decimal import localcontext
    rows = bars()
    expected = runtime.observation_values(rows, '9842.123456789', '.07890123456', '10000')
    with localcontext() as context:
        context.prec = 50
        assert runtime.observation_values(rows, '9842.123456789', '.07890123456', '10000') == expected


def test_hourly_feed_spans_weekend_and_sparse_missing_hours_without_filling(rig):
    engine, account, clock, _ = rig
    starts = [NOW-(100-i)*HOUR-(48*HOUR if i < 98 else 0) for i in range(100)]
    with engine.store.connect() as db:
        db.executemany('INSERT INTO quotes VALUES(?,?,?)',
                       [(start+sample*10000, '2000', '2001') for start in starts for sample in range(360)])
        # An isolated sample during a missing hour must not become a candle.
        db.execute('INSERT INTO quotes VALUES(?,?,?)', (NOW-20*HOUR, '2000', '2001'))
    snapshot = hourly_snapshot(engine.store, clock[0])
    assert len(snapshot['bars']) == 100
    assert [bar.start for bar in snapshot['bars']] == starts
    assert any(b.start-a.start == 49*HOUR for a, b in zip(snapshot['bars'], snapshot['bars'][1:]))
    assert observe(snapshot, account, clock[0])[0] == NOW
    engine.bars_provider = runtime.HourlyFeed(engine.store)
    result = command(engine, account)
    assert Decimal(result['quantity']) > 0


def test_closed_hour_cache_shared_across_decisions_and_invalidates_on_next_hour(rig, monkeypatch):
    engine, _, clock, _ = rig
    calls = []
    def snapshot(store, now):
        calls.append(now)
        return {'source': SOURCE, 'bars': tuple(bars(end=now//HOUR*HOUR))}
    monkeypatch.setattr(runtime, 'hourly_snapshot', snapshot)
    feed = runtime.HourlyFeed(engine.store)
    first = feed(clock[0])
    assert feed(clock[0]+10000) is first
    assert feed(clock[0]+50000) is first and len(calls) == 1
    assert feed(clock[0]+HOUR) is not first and len(calls) == 2


def v2_actor(arm='B'):
    layers = [{'kind': 'linear', 'weight': [[1.]+[0.]*9, [0.]*10, [0.]*10], 'bias': [0., .1, -.1]}]
    return {'version': runtime.V2_PROTOCOL, 'features': list(FEATURES), 'actions': list(TARGETS), 'layers': layers,
            'input': 'raw_f64_observation', 'arm': arm, 'arithmetic': 'python_f64_fsum_tanh_margin_1e-6',
            'min_logit_margin': 1e-6, 'native_actor_sha256': '1'*64, 'dataset': '2'*64,
            'train_end': NOW-1000*HOUR, 'seed': 7, 'timesteps': 131072, 'weights_sha256': digest(layers),
            'scaler': None if arm == 'A' else {'features': list(FEATURES[:7]), 'mean': [0.]*7, 'std': [.1]*7,
                     'ddof': 0, 'clip': [-10, 10], 'fit': 'training_decision_observations_only',
                     'samples': 5038, 'first_observed_at': NOW-3000*HOUR,
                     'last_observed_at': NOW-1001*HOUR, 'train_end': NOW-1000*HOUR}}


@pytest.mark.parametrize('arm,expected', [('A', 1), ('B', 0), ('C', 1)])
def test_v2_frozen_scale_ablations_are_explicit_f64(arm, expected):
    actor = v2_actor(arm)
    runtime.validate_actor(actor)
    raw = [.02]+[0.]*6+[1., 0., 0.]
    processed = runtime.preprocess(actor, raw)
    assert processed[7:] == raw[7:]
    if arm == 'B':
        assert processed[0] == pytest.approx(.2)
    if arm == 'C':
        assert processed[:7] == [0.]*7
    assert infer(actor, raw, runtime.time.monotonic()+1) == expected


@pytest.mark.parametrize('tamper', ['missing', 'zero', 'nonfinite', 'order', 'cutoff', 'ddof', 'hash', 'native', 'input'])
def test_v2_rejects_missing_incompatible_or_noncausal_scaler_metadata(tamper):
    actor = v2_actor()
    if tamper == 'missing':
        actor['scaler'] = None
    elif tamper == 'zero':
        actor['scaler']['std'][0] = 0.
    elif tamper == 'nonfinite':
        actor['scaler']['mean'][0] = float('nan')
    elif tamper == 'order':
        actor['scaler']['features'].reverse()
    elif tamper == 'cutoff':
        actor['scaler']['last_observed_at'] = actor['train_end']
    elif tamper == 'ddof':
        actor['scaler']['ddof'] = False
    elif tamper == 'hash':
        actor['weights_sha256'] = '3'*64
    elif tamper == 'native':
        actor['version'] = 'gold-ppo-input-ablation-v2'
    else:
        actor['input'] = 'raw_v1_observation'
    with pytest.raises(Rejected):
        runtime.validate_actor(actor)


def test_v2_scale_is_pinned_in_actor_hash_and_account_protocol(rig):
    engine, _, clock, root = rig
    actor = v2_actor('C')
    actor_hash = write_json(root/'actor.json', actor)
    manifest = json.loads((root/'fixture.json').read_text())
    manifest.update(protocol=runtime.V2_PROTOCOL, actor_sha256=actor_hash)
    context = digest({'protocol': runtime.V2_PROTOCOL, 'feature_version': FEATURE_VERSION,
                      'source': SOURCE, 'account_config': runtime.money_config(CONFIG)})
    manifest['qualification_sha256'] = write_json(root/'qualification.json', {
        'eligible': True, 'reasons': [], 'synthetic_fixture': True, 'actor_sha256': actor_hash, 'context_hash': context})
    write_json(root/'fixture.json', manifest)
    clock[0] = NOW-1000
    account = engine.create('alice', CONFIG, 'fixture', str(uuid4()), True)
    assert account['config']['policy']['protocol'] == runtime.V2_PROTOCOL
    clock[0] = NOW+1000
    filled = command(engine, account)
    assert Decimal(filled['quantity']) > 0
    details = read(engine, account)['events'][0]['details']
    assert details['protocol'] == runtime.V2_PROTOCOL and len(details['processed_observation_hash']) == 64
    actor['scaler']['mean'][0] = 1.
    write_json(root/'actor.json', actor)
    assert engine.registry.available(clock[0]) == []


def test_status_distinguishes_disabled_unqualified_warmup_and_ready_no_artifact_leak(rig):
    engine, _, clock, root = rig
    engine.enabled = False
    status = engine.status()
    assert status['status'] == 'DISABLED' and status['warmup_status'] == 'READY'
    assert set(status['models'][0]) == {'id', 'protocol', 'feature_version', 'valid_until', 'account_config'}
    engine.enabled = True
    assert engine.status()['status'] == 'READY'
    engine.bars_provider = lambda now: {'source': SOURCE, 'bars': bars(99)}
    status = engine.status()
    assert status['status'] == 'WAITING_HISTORY' and status['complete_bars'] == 99
    engine.bars_provider = lambda now: {'source': SOURCE, 'bars': bars()}
    clock[0] += HOUR
    assert engine.status()['status'] == 'WAITING_FRESH_CLOSED_HOUR'
    register(root, eligible=False)
    status = engine.status()
    assert status['models'] == [] and status['status'] == 'NO_QUALIFIED_MODELS'


def test_status_api_default_off_and_empty_actual_data(tmp_path):
    app = create_app(tmp_path/'status.db', background=False, policy_enabled=False, clock=lambda: NOW)
    with TestClient(app) as client:
        response = client.get('/api/v1/gold/policy/status')
        assert response.status_code == 200
        assert response.json() == {'enabled': False, 'trading_enabled': False, 'source': SOURCE,
            'required_bars': 100, 'complete_bars': 0, 'latest_close_at': None,
            'warmup_status': 'WAITING_HISTORY', 'status': 'DISABLED', 'models': [], 'checked_at': NOW}


def test_decision_expires_during_inference_no_order(rig, monkeypatch):
    engine, account, clock, _ = rig
    def late(*_):
        clock[0] = NOW+runtime.MAX_AGE+1
        return 1
    monkeypatch.setattr(runtime, 'infer', late)
    assert command(engine, account)['quantity'] == '0'
    assert read(engine, account)['events'][0]['details']['reason'] == 'EXPIRED_DECISION'


def test_v1_cannot_silently_ignore_attached_v2_scaler(rig):
    engine, _, clock, _ = rig
    actor = engine.registry.get('fixture', clock[0]).actor
    actor['scaler'] = v2_actor()['scaler']
    with pytest.raises(Rejected, match='UNSUPPORTED_MODEL_CONTRACT'):
        runtime.validate_actor(actor)


def test_preprocessing_input_boundaries_no_nonfinite_or_double_scale():
    actor = v2_actor('B')
    runtime.validate_actor(actor)
    assert runtime.preprocess(actor, [100.]*7+[1., 0., 0.]) == [10.]*7+[1., 0., 0.]
    for invalid in ([float('nan')]*10, [float('inf')]*10, [True]*10, [0.]*9):
        with pytest.raises(Rejected, match='INVALID_OBSERVATION'):
            runtime.preprocess(actor, invalid)
    actor['arm'] = 'A'
    with pytest.raises(Rejected, match='ARM_A_REQUIRES_NO_SCALER'):
        runtime.validate_actor(actor)


def test_late_fresh_closing_sample_invalidates_cached_partial_hour(tmp_path):
    store = Store(tmp_path/'late.db')
    with store.connect() as db:
        db.executemany('INSERT INTO quotes VALUES(?,?,?)',
                       [(NOW-HOUR+i*10000, '2000', '2001') for i in range(358)])
    feed = runtime.HourlyFeed(store)
    assert feed(NOW+1000)['bars'] == ()
    store.put_quote(Quote(NOW-10000, Decimal(2000), Decimal(2001)), NOW+1000)
    assert len(feed(NOW+1000)['bars']) == 1


def test_timer_waiting_can_retry_newly_observed_prefix_but_never_two_decisions(rig):
    engine, account, clock, _ = rig
    provider = engine.bars_provider
    engine.bars_provider = lambda now: {'source': SOURCE, 'bars': bars(99)}
    engine.advance()
    assert read(engine, account)['quantity'] == '0'
    engine.bars_provider = provider
    # Simulate a final late-but-fresh closing sample arriving before the first current-hour sample.
    with engine.store.connect() as db:
        db.execute('INSERT INTO quotes VALUES(?,?,?)', (NOW-10000, '2000', '2001'))
    engine.advance()
    filled = read(engine, account)
    assert Decimal(filled['quantity']) > 0
    engine.advance()
    assert read(engine, account)['revision'] == filled['revision']


def test_status_never_silently_truncates_available_models(rig):
    engine, _, _, root = rig
    for i in range(513):
        (root/f'other-{i}.txt').touch()
    with pytest.raises(Rejected, match='MODEL_REGISTRY_CAPACITY_EXCEEDED'):
        engine.status()


def test_request_receipt_preserves_original_states_after_revocation_stop_and_disable(rig, monkeypatch):
    engine, account, clock, root = rig
    with engine.store.connect() as db:
        create_key = db.execute('SELECT key FROM events WHERE account_id=? AND seq=1', (account['id'],)).fetchone()[0]
    body = {'request_id': str(uuid4()), 'expected_revision': 1, 'action': 'DECIDE'}
    bought = engine.command(account['id'], 'alice', body)
    stopped = command(engine, bought, action='STOP')
    register(root, eligible=False)
    clock[0] += 101*HOUR
    engine.enabled = False
    def forbidden(*_):
        raise AssertionError('Historical receipt lookup must not access models or observations')
    monkeypatch.setattr(engine.registry, 'get', forbidden)
    engine.bars_provider = forbidden
    before = read(engine, account)
    created_receipt = engine.request_receipt('alice', create_key)
    assert created_receipt['found'] is True and created_receipt['kind'] == 'CREATED'
    assert created_receipt['state'] == account and created_receipt['state']['quantity'] == '0'
    assert created_receipt['hash_scope'] == 'ACCOUNT_CONFIG'
    assert created_receipt['request_hash'] == digest(account['config'])
    assert created_receipt['state']['config']['policy']['model_id'] == 'fixture'
    decision_receipt = engine.request_receipt('alice', body['request_id'])
    assert decision_receipt['kind'] == 'POLICY_DECIDE' and decision_receipt['state'] == bought
    assert decision_receipt['request_hash'] == digest(body) and decision_receipt['hash_scope'] == 'COMMAND_BODY'
    assert decision_receipt['details']['fill']['simulated'] is True
    assert decision_receipt['account'] == stopped and decision_receipt['state']['revision'] < stopped['revision']
    assert read(engine, account) == before


@pytest.mark.parametrize('operation', ['CREATE', 'DECIDE'])
def test_request_not_found_does_not_resolve_concurrent_inflight_write(rig, monkeypatch, operation):
    engine, account, _, _ = rig
    key = str(uuid4())
    started, release = Event(), Event()
    def block():
        started.set()
        assert release.wait(3)
    if operation == 'CREATE':
        original = engine.store._write
        def pending(*args):
            original(*args)
            block()
        monkeypatch.setattr(engine.store, '_write', pending)
        submit = lambda: engine.create('alice', CONFIG, 'fixture', key, True)
    else:
        def pending(*_):
            block()
            return 1
        monkeypatch.setattr(runtime, 'infer', pending)
        engine.inference_ms = 5000
        submit = lambda: command(engine, account, key=key)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(submit)
        assert started.wait(3)
        try:
            assert engine.request_receipt('alice', key) == {
                'found': False, 'request_id': key, 'absence_proves_not_applied': False}
        finally:
            release.set()
        committed = future.result(timeout=3)
    receipt = engine.request_receipt('alice', key)
    assert receipt['found'] is True and receipt['account_id'] == committed['id']
    assert receipt['state'] == committed


def test_request_lookup_uses_one_read_snapshot_during_concurrent_control(rig, monkeypatch):
    engine, account, _, _ = rig
    with engine.store.connect() as db:
        key = db.execute('SELECT key FROM events WHERE account_id=? AND seq=1', (account['id'],)).fetchone()[0]
    original = engine.store._read
    started, release = Event(), Event()
    def pause_reader(*args):
        if not started.is_set():
            started.set()
            assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(engine.store, '_read', pause_reader)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(engine.request_receipt, 'alice', key)
        assert started.wait(3)
        try:
            paused = command(engine, account, action='PAUSE')
        finally:
            release.set()
        receipt = future.result(timeout=3)
    assert receipt['state'] == account and receipt['account'] == account
    assert paused['revision'] == 2 and read(engine, account)['revision'] == 2


def test_request_lookup_rejects_ambiguous_account_scoped_uuid(rig):
    engine, first, _, _ = rig
    second = engine.create('alice', CONFIG, 'fixture', str(uuid4()), True)
    key = str(uuid4())
    command(engine, first, action='PAUSE', key=key)
    command(engine, second, action='PAUSE', key=key)
    with pytest.raises(Conflict, match='multiple policy accounts'):
        engine.request_receipt('alice', key)


def test_request_lookup_verifies_historical_event_not_only_latest_state(rig):
    engine, account, _, _ = rig
    command(engine, account, action='PAUSE')
    # Isolated corruption fixture: normal ledger triggers prohibit this update.
    with engine.store.connect() as db:
        row = db.execute('SELECT key,payload FROM events WHERE account_id=? AND seq=1', (account['id'],)).fetchone()
        payload = json.loads(row['payload'])
        payload['state']['cash'] = '999999'
        db.execute('DROP TRIGGER gold_events_no_update')
        db.execute('UPDATE events SET payload=? WHERE account_id=? AND seq=1', (canonical(payload), account['id']))
    with pytest.raises(ValueError, match='receipt integrity'):
        engine.request_receipt('alice', row['key'])


def test_receipt_api_owner_boundary_legacy_exclusion_and_revocation(tmp_path, monkeypatch):
    root = tmp_path/'registry'
    root.mkdir()
    register(root)
    app = create_app(tmp_path/'receipt.db', background=False, secure=True, clock=lambda: NOW,
                     policy_registry_path=root, policy_enabled=True)
    app.state.policy.registry.allow_fixtures = True
    key = str(uuid4())
    payload = {**CONFIG, 'mode': 'POLICY', 'model_id': 'fixture', 'experimental': True, 'request_id': key}
    url = f'/api/v1/gold/policy/requests/{key}'
    with TestClient(app, base_url='https://testserver') as alice, TestClient(app, base_url='https://testserver') as bob:
        created = alice.post('/api/v1/gold/policy/accounts', json=payload)
        assert created.status_code == 200
        assert bob.get(url).status_code == 401
        bob.get('/api/v1/gold/accounts')
        assert bob.get(url).json() == {'found': False, 'request_id': key, 'absence_proves_not_applied': False}
        # The same UUID in a different owner scope must resolve only that owner's receipt.
        bob_created = bob.post('/api/v1/gold/policy/accounts', json=payload)
        assert bob_created.status_code == 200 and bob_created.json()['id'] != created.json()['id']
        assert bob.get(url).json()['account_id'] == bob_created.json()['id']
        pause_key = str(uuid4())
        paused = alice.post(f"/api/v1/gold/policy/accounts/{created.json()['id']}/command",
                            json={'request_id': pause_key, 'expected_revision': 1, 'action': 'PAUSE'})
        assert paused.status_code == 200
        reused = alice.post('/api/v1/gold/policy/accounts', json={**payload, 'request_id': pause_key})
        assert reused.status_code == 409 and 'Create request ID' in reused.json()['detail']
        assert len(alice.get('/api/v1/gold/policy/accounts').json()['accounts']) == 1
        legacy_key = str(uuid4())
        legacy = alice.post('/api/v1/gold/accounts', json={**CONFIG, 'mode': 'MANUAL', 'request_id': legacy_key})
        assert legacy.status_code == 200
        assert alice.get(f'/api/v1/gold/policy/requests/{legacy_key}').json()['found'] is False
        app.state.policy.enabled = False
        def forbidden(*_):
            raise AssertionError('Recovery must not check revoked registry')
        monkeypatch.setattr(app.state.policy.registry, 'get', forbidden)
        receipt = alice.get(url)
        assert receipt.status_code == 200 and receipt.json()['found'] is True
        assert receipt.json()['account_id'] == created.json()['id']
        assert receipt.json()['state']['config']['policy']['model_id'] == 'fixture'
        assert receipt.headers['cache-control'] == 'no-store'
        assert alice.get('/api/v1/gold/policy/requests/not-a-uuid').status_code == 422


def test_request_lookup_verifies_command_hash_column_independently(rig):
    engine, account, _, _ = rig
    key = str(uuid4())
    paused = command(engine, account, action='PAUSE', key=key)
    command(engine, paused, action='STOP')
    # Isolated corruption fixture: request_hash is not itself inside payload.
    with engine.store.connect() as db:
        db.execute('DROP TRIGGER gold_events_no_update')
        db.execute('UPDATE events SET request_hash=? WHERE account_id=? AND key=?', ('0'*64, account['id'], key))
    with pytest.raises(ValueError, match='command receipt integrity'):
        engine.request_receipt('alice', key)


def test_create_rejects_uuid_already_used_for_pause(rig):
    engine, account, _, _ = rig
    key = str(uuid4())
    paused = command(engine, account, action='PAUSE', key=key)
    before = read(engine, account)
    with pytest.raises(Conflict, match='Create request ID'):
        engine.create('alice', CONFIG, 'fixture', key, True)
    assert read(engine, account) == before and before['revision'] == paused['revision']


def test_create_retries_original_receipt_across_hours_after_later_commands(rig):
    engine, _, clock, _ = rig
    key = str(uuid4())
    created = engine.create('alice', CONFIG, 'fixture', key, True)
    paused = command(engine, created, action='PAUSE')
    clock[0] += 2*HOUR
    assert engine.create('alice', CONFIG, 'fixture', key, True) == paused
    assert engine.request_receipt('alice', key)['state'] == created
    assert read(engine, created)['revision'] == 2


def test_create_rejects_changed_creation_request_hash(rig):
    engine, _, _, _ = rig
    key = str(uuid4())
    created = engine.create('alice', CONFIG, 'fixture', key, True)
    with engine.store.connect() as db:
        db.execute('DROP TRIGGER gold_events_no_update')
        db.execute('UPDATE events SET request_hash=? WHERE account_id=? AND key=?', ('0'*64, created['id'], key))
    with pytest.raises(Conflict, match='Create request ID'):
        engine.create('alice', CONFIG, 'fixture', key, True)


def test_create_foreign_command_uuid_does_not_conflict_or_return_foreign_account(rig):
    engine, alice, _, _ = rig
    key = str(uuid4())
    paused = command(engine, alice, action='PAUSE', key=key)
    bob = engine.create('bob', CONFIG, 'fixture', key, True)
    assert bob['id'] != alice['id'] and bob['revision'] == 1 and bob['status'] == 'ACTIVE'
    assert engine.create('bob', CONFIG, 'fixture', key, True) == bob
    assert engine.request_receipt('bob', key)['account_id'] == bob['id']
    assert engine.request_receipt('alice', key)['kind'] == 'POLICY_PAUSE'
    assert read(engine, alice)['revision'] == paused['revision']


def test_create_rejects_command_collision_even_after_matching_created_receipt(rig):
    engine, _, _, _ = rig
    key = str(uuid4())
    first = engine.create('alice', CONFIG, 'fixture', key, True)
    second = engine.create('alice', CONFIG, 'fixture', str(uuid4()), True)
    command(engine, second, action='PAUSE', key=key)
    with pytest.raises(Conflict, match='Create request ID'):
        engine.create('alice', CONFIG, 'fixture', key, True)
    assert read(engine, first)['revision'] == 1


def test_same_creation_uuid_concurrent_retries_create_exactly_one_account(rig):
    engine, _, _, _ = rig
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: engine.create('alice', CONFIG, 'fixture', key, True), range(4)))
    assert len({row['id'] for row in results}) == 1
    assert all(row['revision'] == 1 for row in results)
    assert len(engine.store.accounts('alice')) == 2  # original fixture plus one new account
    assert engine.request_receipt('alice', key)['seq'] == 1
