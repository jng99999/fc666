"""Synthetic cost-study contracts; these tests do not train or qualify any model."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

pytest.importorskip('gymnasium')
import numpy as np

from core.gold.history import HOUR, canonical
from core.gold.rl_experiments import FrozenScaler, VERSION
from scripts import study_gold_ppo_cost_stress as study
from tests.test_gold_rl_experiments import varied_bars


def declared_protocol():
    return study.load_protocol(study.ROOT/'docs/research/ppo-cost-stress-protocol.json')[0]


def row(arm, seed, scenario, gain='.2', fee='1', fixed_action=None):
    start = study.moment('2024-01-01')
    gain, fee = Decimal(gain), Decimal(fee)
    cash = arm == 'fixed' and fixed_action == 0
    if cash:
        gain = fee = Decimal(0)
    final = Decimal(10000)*(1+gain)
    fills = [] if cash else [{'side': 'BUY', 'decision_at': start,
        'execution_at': start+1000, 'bid': '1000', 'ask': '1000.10',
        'price': '1000.11', 'ounces': '1', 'fee': str(fee), 'cash_after': str(Decimal('8999.89')-fee),
        'quantity_after': '1', 'simulated': True}]
    counts = [2, 6, 2]
    if arm == 'fixed':
        counts = [10 if i == fixed_action else 0 for i in range(3)]
    result = {'arm': arm, 'seed': seed, 'scenario': scenario,
        'window': 'validation_2024', 'trading_enabled': False,
        'metrics': {'total_return': str(gain), 'max_drawdown': '.01',
            'fees': str(fee), 'spread_cost': '0' if cash else '.05',
            'slippage_cost': '0' if cash else '.01', 'final_equity': str(final),
            'net_pnl': str(final-10000), 'realized_pnl': '0',
            'open_ounces': '0' if cash else '1', 'fill_count': len(fills), 'expired_gap_intents': 0},
        'fills': fills, 'equity': [{'at': start+(i+1)*HOUR,
            'equity': str(Decimal(10000)+(final-10000)*Decimal(i+1)/10)} for i in range(10)],
        'action_counts': None if arm == 'hold' else counts,
        'effective_start': start, 'effective_end': start+10*HOUR}
    if arm != 'hold':
        result['json_actor_parity_steps'] = 0 if arm == 'fixed' else 10
    if arm == 'fixed':
        result['fixed_action'] = fixed_action
    return result


def criterion_rows():
    rows = []
    for scenario in study.SCENARIOS:
        for arm, gain, fee in [('B', '.2', '1'), ('C', '.05', '1'),
                               ('D', '.3', '.4'), ('E', '.1', '.8')]:
            rows.extend(row(arm, seed, scenario, gain, fee) for seed in study.SEEDS)
        rows.extend(row('fixed', None, scenario, gain, fixed_action=a)
                    for a, gain in enumerate(('0', '.08', '.12')))
        rows.append(row('hold', None, scenario, '.15'))
    return rows


def test_protocol_is_exact_predeclared_bytes_not_a_runtime_override(tmp_path):
    source = study.ROOT/'docs/research/ppo-cost-stress-protocol.json'
    protocol, raw = study.load_protocol(source)
    assert study.digest(raw) == study.PROTOCOL_SHA256
    assert protocol['seeds'] == [7, 19, 42] and protocol['new_training_runs'] == 6
    for bad in (raw+b'\n', raw.replace(b'131072', b'262144')):
        target = tmp_path/'protocol.json'
        target.write_bytes(bad)
        with pytest.raises(ValueError, match='protocol SHA'):
            study.load_protocol(target)


def test_duplicate_json_keys_and_nonfinite_json_numbers_rejected():
    with pytest.raises(ValueError, match='Duplicate JSON'):
        study.read_json(b'{"fees":1,"fees":0}')
    with pytest.raises(ValueError, match='Nonfinite'):
        study.read_json(b'{"fees":NaN}')


def test_training_cost_is_independent_of_both_evaluation_scenarios():
    protocol = declared_protocol()
    trained = study.Costs.model_validate(protocol['training_costs'])
    evaluated = dict(study.evaluation_costs(protocol['configured_costs']))
    assert trained.commission == Decimal('.20')
    assert evaluated['configured_costs'].commission == Decimal('.10')
    assert evaluated['double_cost_stress'].commission == Decimal('.20')
    assert [c.spread_multiplier for c in evaluated.values()] == [Decimal(1), Decimal(2)]
    protocol['training_costs']['commission'] = '.80'
    protocol['training_costs']['spread_multiplier'] = '4'
    assert dict(study.evaluation_costs(protocol['configured_costs'])) == evaluated


def test_all_seeds_are_required_and_no_qualification_even_when_every_check_passes():
    rows = criterion_rows()
    outcome = study.interpret(rows)
    assert len(rows) == 32
    assert outcome['H3']['supported'] and outcome['market_increment']['demonstrated']
    assert all(r['exploratory_conditions_met'] for r in outcome['legacy_gate_diagnostics'])
    assert not outcome['promotion_authorized'] and not outcome['live_trading']
    assert all(not r['admission_authorized'] for r in outcome['legacy_gate_diagnostics'])
    failed = next(r for r in rows if r['arm'] == 'D' and r['seed'] == 42
                  and r['scenario'] == 'double_cost_stress')
    failed['metrics']['fees'] = '2'
    assert not study.interpret(rows)['H3']['supported']
    # The other seeds still improve; a positive median is not the declared claim.
    assert sum(all(r['checks'].values()) for r in study.interpret(rows)['H3']['cases']) == 2


def test_one_market_seed_failure_is_not_hidden_by_other_seeds():
    rows = criterion_rows()
    failed = next(r for r in rows if r['arm'] == 'E' and r['seed'] == 42
                  and r['scenario'] == 'configured_costs')
    failed['metrics']['max_drawdown'] = '.001'
    outcome = study.interpret(rows)
    assert outcome['H3']['supported']
    assert not outcome['market_increment']['demonstrated']


def test_cash_collapse_cannot_support_cost_hypothesis():
    rows = criterion_rows()
    for item in rows:
        if item['arm'] == 'B':
            item['metrics']['total_return'] = '-.1'
        elif item['arm'] == 'D':
            item.update(action_counts=[10, 0, 0], fills=[])
            item['metrics'].update(total_return='0', fees='0', spread_cost='0',
                slippage_cost='0', fill_count=0, open_ounces='0', final_equity='10000', net_pnl='0')
            for point in item['equity']:
                point['equity'] = '10000'
    result = study.interpret(rows)
    assert not result['H3']['supported']
    assert all(r['checks']['return_gt_control'] and r['checks']['explicit_cost_lt_control']
               and not r['checks']['has_fills'] and not r['checks']['at_least_two_actions']
               for r in result['H3']['cases'])


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown_seed', 'missing_fixed', 'duplicate_hold'])
def test_missing_duplicate_or_unplanned_cases_refused(mutation):
    rows = criterion_rows()
    if mutation == 'missing':
        rows.pop(0)
    elif mutation == 'duplicate':
        rows.append(deepcopy(rows[0]))
    elif mutation == 'unknown_seed':
        rows[0]['seed'] = 123
    elif mutation == 'missing_fixed':
        rows = [r for r in rows if not (r['arm'] == 'fixed' and r['fixed_action'] == 1)]
    else:
        rows.append(deepcopy(next(r for r in rows if r['arm'] == 'hold')))
    with pytest.raises(ValueError):
        study.interpret(rows)


@pytest.mark.parametrize('field,value', [
    ('fees', 'NaN'), ('total_return', 'Infinity'), ('max_drawdown', '-.1'),
    ('fill_count', True), ('fill_count', 1.0), ('expired_gap_intents', -1),
    ('extra_metric', 'NaN'), ('final_equity', '0'), ('spread_cost', '-.001')])
def test_nonfinite_missing_or_invalid_metrics_refused(field, value):
    rows = criterion_rows()
    rows[0]['metrics'][field] = value
    with pytest.raises(ValueError):
        study.interpret(rows)


@pytest.mark.parametrize('counts', [[True, 8, 1], [1.0, 8, 1], [0, 0, 0], [1, 2], [-1, 10, 1]])
def test_invalid_action_counts_refused(counts):
    rows = criterion_rows()
    rows[0]['action_counts'] = counts
    with pytest.raises(ValueError, match='action counts'):
        study.interpret(rows)


def test_missing_metric_incomplete_curve_and_missing_parity_refused():
    for name in ('total_return', 'fees', 'fill_count'):
        rows = criterion_rows()
        del rows[0]['metrics'][name]
        with pytest.raises(ValueError):
            study.interpret(rows)
    for key in ('fills', 'equity', 'json_actor_parity_steps'):
        rows = criterion_rows()
        del rows[0][key]
        with pytest.raises(ValueError):
            study.interpret(rows)


def baseline_fixture(directory):
    protocol = declared_protocol()
    scaler = FrozenScaler((0.,)*7, (1.,)*7, 100,
        study.moment('2023-02-01'), study.moment('2023-12-01'), study.moment('2024-01-01'))
    original = [r for r in criterion_rows() if r['arm'] in ('B', 'C', 'fixed', 'hold')]
    original += [row('A', seed, scenario) for seed in study.SEEDS for scenario in study.SCENARIOS]
    baseline = {'version': VERSION, 'dataset': protocol['dataset'],
        'train_start': study.moment(protocol['train_start']), 'train_end': study.moment(protocol['train_end']),
        'validation_end': study.moment(protocol['exploratory_end']), 'seeds': protocol['seeds'],
        'timesteps_per_run': protocol['timesteps_per_run'], 'hyperparameters': protocol['hyperparameters'],
        'code_sha256': protocol['frozen_code_sha256'], 'costs': protocol['configured_costs'],
        'scaler': scaler.payload()}
    artifacts = {'protocol.json': baseline, 'scaler.json': scaler.payload(),
        'study.json': {'protocol': baseline, 'results': original, 'trading_enabled': False}}
    for name, payload in artifacts.items():
        raw = canonical(payload)
        (directory/name).write_bytes(raw)
        protocol['baseline_sha256'][name] = study.digest(raw)
    return protocol, artifacts


@pytest.mark.parametrize('name', ['protocol.json', 'study.json', 'scaler.json'])
def test_baseline_full_inventory_hash_and_scaler_contract(tmp_path, name):
    protocol, artifacts = baseline_fixture(tmp_path)
    baseline, scaler, controls = study.load_baseline(tmp_path, protocol)
    assert canonical(baseline['scaler']) == canonical(scaler.payload())
    assert len(controls) == 20 and {r['arm'] for r in controls} == {'B', 'C', 'fixed', 'hold'}
    assert all(r['provenance']['recomputed'] is False for r in controls)
    # Even a harmless whitespace change fails the already bound baseline receipt.
    (tmp_path/name).write_bytes(canonical(artifacts[name])+b'\n')
    with pytest.raises(ValueError, match='baseline SHA'):
        study.load_baseline(tmp_path, protocol)


def test_control_actor_receipt_requires_published_manifest_digest(tmp_path, monkeypatch):
    manifest = {}
    for arm in study.ARMS.values():
        for seed in study.SEEDS:
            name = f'actor-{arm}-seed-{seed}.json'
            raw = canonical({'synthetic_actor': name})
            (tmp_path/name).write_bytes(raw)
            manifest[name] = study.digest(raw)
    raw = canonical(manifest)
    (tmp_path/'manifest.json').write_bytes(raw)
    monkeypatch.setattr(study, 'CONTROL_MANIFEST_SHA256', study.digest(raw))
    receipt = study.control_actor_origins(tmp_path)
    assert len(receipt['actor_sha256']) == 6
    assert receipt['loaded_pickle_or_executed_controls'] is False
    (tmp_path/'actor-B-seed-7.json').write_bytes(b'changed')
    with pytest.raises(ValueError, match='control actor SHA'):
        study.control_actor_origins(tmp_path)
    # Rehashing the mutable adjacent manifest does not repair its published identity.
    manifest['actor-B-seed-7.json'] = study.digest(b'changed')
    (tmp_path/'manifest.json').write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match='published control manifest'):
        study.control_actor_origins(tmp_path)


def test_frozen_source_and_dependency_fail_before_runtime_use(tmp_path, monkeypatch):
    protocol = declared_protocol()
    root = tmp_path/'source'
    root.mkdir()
    (root/'only.py').write_bytes(b'known')
    protocol['frozen_code_sha256'] = {'only.py': study.digest(b'known')}
    monkeypatch.setattr(study.subprocess, 'check_output', lambda *a, **k: b'changed dependency')
    with pytest.raises(ValueError, match='dependencies SHA'):
        study.verify_environment(protocol, {}, root)
    (root/'only.py').write_bytes(b'changed source')
    with pytest.raises(ValueError, match='source SHA'):
        study.verify_environment(protocol, {}, root)


def test_actual_training_prefix_scaler_and_no_2025_feature_computation(monkeypatch):
    protocol = declared_protocol()
    split, end = study.moment('2024-01-01'), study.moment('2025-01-01')
    shift = study.moment('2023-01-01')-split
    bars = [replace(b, start=b.start+(shift if i < 170 else 0),
                    first=b.first+(shift if i < 170 else 0), last=b.last+(shift if i < 170 else 0))
            for i, b in enumerate(varied_bars())]
    future = replace(bars[-1], start=end, first=end+1000, last=end+HOUR-1000,
                     bid_close=Decimal(99999), ask_close=Decimal(100000))
    scaler = FrozenScaler.fit(bars[:170], study.moment('2023-01-01'), split)
    baseline = {'train_bars': 170, 'validation_bars': 70}
    payload = {'start': study.moment('2023-01-01'), 'end': end+HOUR,
               'opens': [[b.start, b.first, str(b.bid_open), str(b.ask_open)] for b in bars+[future]]}
    class Catalogue:
        def __init__(self, path):
            pass
        def load(self, identity):
            assert identity == protocol['dataset']
            return payload, bars+[future]
    monkeypatch.setattr(study, 'Catalogue', Catalogue)
    original, seen_ends = study.market_features, []
    def features(rows):
        seen_ends.append(rows[-1].start+HOUR)
        return original(rows)
    monkeypatch.setattr(study, 'market_features', features)
    prepared = study.prepare_data('synthetic', protocol, baseline, scaler)
    assert len(prepared['bars']) == 240 and all(last <= end for last in seen_ends)
    assert prepared['prefix_proof']['raw_equal']
    for proof in prepared['prefix_proof']['transforms'].values():
        assert proof['training_sha256'] == proof['exploration_prefix_sha256']
    np.testing.assert_array_equal(prepared['transforms']['C'][0], np.zeros((170, 7)))
    with pytest.raises(ValueError, match='scaler'):
        study.prepare_data('synthetic', protocol, baseline, replace(scaler, mean=(1.,)*7))


def test_model_manifest_binds_new_identity_training_costs_and_artifact_bytes(tmp_path):
    protocol = declared_protocol()
    actor = {'version': VERSION, 'arm': 'B', 'seed': 7, 'dataset': protocol['dataset'],
        'train_end': study.moment('2024-01-01'), 'timesteps': 131072}
    actor_path, zip_path = tmp_path/'actor-D-seed-7.json', tmp_path/'own.zip'
    actor_path.write_bytes(canonical(actor)); zip_path.write_bytes(b'synthetic own zip, never loaded')
    identity = study.model_identity('D', 7, actor_path, zip_path, protocol, study.PROTOCOL_SHA256)
    assert identity['study_arm'] == 'D' and identity['native_input_arm'] == 'B'
    assert identity['training_costs']['commission'] == '0.20'
    assert identity['evaluation_costs']['configured_costs']['commission'] == '0.10'
    assert identity['actor_sha256'] == study.digest(actor_path.read_bytes())
    assert identity['ppo_sha256'] == study.digest(zip_path.read_bytes())
    assert identity['protocol_sha256'] == study.PROTOCOL_SHA256
    assert not identity['promotion_authorized'] and not identity['deployment_evidence_inherited']
    with pytest.raises(ValueError, match='identity'):
        study.model_identity('E', 7, actor_path, zip_path, protocol, study.PROTOCOL_SHA256)
    with pytest.raises(ValueError, match='identity'):
        study.model_identity('D', 7, actor_path, zip_path, protocol, '0'*64)


@pytest.mark.parametrize('native', ['B', 'C'])
def test_own_saved_untrained_ppo_actor_has_stepwise_native_parity(native, tmp_path, monkeypatch):
    """Exercise actual serialization/evaluation without any optimizer/training step."""
    from core.gold.rl import market_features
    from core.gold.rl_experiments import actor_action, transform_market
    from scripts.study_gold_ppo import export_actor
    from tests.test_gold_rl import environment, START
    def forbidden(*args, **kwargs):
        raise AssertionError('No training authorized in these tests')
    monkeypatch.setattr(study.PPO, 'learn', forbidden)
    study.torch.set_num_threads(1)
    rows = varied_bars()
    scaler = FrozenScaler.fit(rows, START, rows[-1].start+HOUR)
    raw = market_features(rows)
    transformed = transform_market(raw, native, scaler)
    env = environment(rows, features=transformed)
    model = study.PPO('MlpPolicy', env, n_steps=32, batch_size=32,
        policy_kwargs={'net_arch': [32, 32]}, seed=7, device='cpu', verbose=0)
    path = tmp_path/'own-untrained.zip'
    model.save(path)
    model = study.PPO.load(path, device='cpu')
    assert model.num_timesteps == 0
    actor = {**export_actor(model), 'version': VERSION, 'arm': native,
             'input': 'raw_v1_observation', 'scaler': scaler.payload()}
    actor_path = tmp_path/'own-untrained.json'
    study.write_json(actor_path, actor)
    actor = study.read_json(actor_path.read_bytes())
    result = study.evaluate(env, model=model, actor=actor, raw_features=raw)
    assert result['json_actor_parity_steps'] == len(rows)-100
    assert len(result['equity']) == len(rows)-100
    assert result['trading_enabled'] is False
    class WrongPPO:
        def predict(self, observation, deterministic):
            action = actor_action(actor, np.concatenate((raw[env.index], observation[7:])))
            return (action+1) % 3, None
    # A mismatching PPO is a hard refusal, never an actor fallback.
    with pytest.raises(ArithmeticError, match='disagrees'):
        study.evaluate(env, model=WrongPPO(), actor=actor, raw_features=raw)


def test_output_directory_never_overwritten_and_tampering_never_starts_training(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Training must not start')
    monkeypatch.setattr(study.PPO, 'learn', forbidden)
    protocol_path = study.ROOT/'docs/research/ppo-cost-stress-protocol.json'
    monkeypatch.setattr(study, 'load_baseline', lambda *a: ({}, None, []))
    monkeypatch.setattr(study, 'verify_environment', lambda *a: (b'', {}))
    monkeypatch.setattr(study, 'control_actor_origins', lambda *a: {})
    monkeypatch.setattr(study, 'prepare_data', lambda *a: {})
    existing = tmp_path/'existing'; existing.mkdir(); (existing/'keep').write_text('old receipt')
    with pytest.raises(FileExistsError):
        study.run_study(history='unused', baseline_directory='unused',
            protocol_path=protocol_path, output=existing)
    assert (existing/'keep').read_text() == 'old receipt'
    bad = tmp_path/'tampered.json'; bad.write_text('{}')
    with pytest.raises(ValueError, match='protocol SHA'):
        study.run_study(history='unused', baseline_directory='unused',
            protocol_path=bad, output=tmp_path/'never-created')
    assert not (tmp_path/'never-created').exists()
