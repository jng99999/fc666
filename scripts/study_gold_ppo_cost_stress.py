"""One preregistered training-cost intervention; exploration never authorizes orders."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.env_checker import check_env
import torch

from core.gold.backtest import Costs
from core.gold.history import Catalogue, HOUR, canonical
from core.gold.rl import FEATURES, GoldEnv, market_features
from core.gold.rl_experiments import FrozenScaler, VERSION, transform_market
from scripts.study_gold_ppo import HYPERPARAMETERS, SEEDS, export_actor, moment
from scripts.study_gold_ppo_v2 import SCENARIOS, evaluate

ROOT = Path(__file__).resolve().parents[1]
STUDY_VERSION = 'gold-ppo-training-cost-intervention-v1'
# Exact predeclared input bytes, not a user-supplied digest that can bless changes.
PROTOCOL_SHA256 = '3dfc0621c4fb0bda412b42c5d5670b7294fbd395371bef03c9bf25d46c017763'
# Already published original v2 proof; a mutable adjacent manifest alone is insufficient.
CONTROL_MANIFEST_SHA256 = '895dd774a30f6526b5219e8a43adff4f47779b92040ae60350573340173300fe'
ARMS = {'D': 'B', 'E': 'C'}
COUNT_METRICS = ('fill_count', 'expired_gap_intents')
DECIMAL_METRICS = ('total_return', 'max_drawdown', 'fees', 'spread_cost',
                   'slippage_cost', 'final_equity', 'net_pnl', 'realized_pnl', 'open_ounces')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def read_json(raw):
    def nonfinite(value):
        raise ValueError(f'Nonfinite JSON value: {value}')
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=nonfinite)


def write_json(path, payload):
    # Same canonical encoding as existing artifacts, with nonfinite numbers refused.
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=True, allow_nan=False).encode()
    with path.open('xb') as handle:
        handle.write(raw)


def load_protocol(path):
    raw = Path(path).read_bytes()
    if digest(raw) != PROTOCOL_SHA256:
        raise ValueError('Predeclared protocol SHA mismatch; do not alter or tune this study')
    protocol = read_json(raw)
    if (protocol['study_version'] != STUDY_VERSION or protocol['seeds'] != list(SEEDS)
            or protocol['arms'] != {arm: {'input_arm': native, 'control_arm': native}
                                    for arm, native in ARMS.items()}
            or protocol['hyperparameters'] != HYPERPARAMETERS
            or protocol['new_training_runs'] != 6 or protocol['timesteps_per_run'] != 131072
            or protocol['evaluation_scenarios'] != list(SCENARIOS)
            or protocol['promotion_authorized'] is not False or protocol['live_trading'] is not False):
        raise ValueError('Frozen training protocol contract mismatch')
    return protocol, raw


def evaluation_costs(configured):
    """Derived only from configured evaluation costs, never from training costs."""
    base = Costs.model_validate(configured)
    stress = Costs.model_validate({**base.model_dump(mode='json'),
        'commission': str(base.commission*2), 'slippage': str(base.slippage*2),
        'spread_multiplier': str(base.spread_multiplier*2)})
    return ((SCENARIOS[0], base), (SCENARIOS[1], stress))


def finite_decimal(value, label):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f'Invalid metric: {label}')
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f'Invalid metric: {label}') from error
    if not number.is_finite():
        raise ValueError(f'Nonfinite metric: {label}')
    return number


def case_key(row):
    arm, seed, scenario = row.get('arm'), row.get('seed'), row.get('scenario')
    if scenario not in SCENARIOS:
        raise ValueError('Unexpected cost scenario')
    if arm in ('B', 'C', 'D', 'E'):
        if type(seed) is not int or seed not in SEEDS:
            raise ValueError('Unexpected seed')
        return arm, seed, scenario
    if seed is not None:
        raise ValueError('Reference must not have a seed')
    if arm == 'fixed':
        action = row.get('fixed_action')
        if type(action) is not int or action not in range(3):
            raise ValueError('Unexpected fixed action')
        return arm, action, scenario
    if arm == 'hold':
        return arm, None, scenario
    raise ValueError('Unexpected experiment arm')


def expected_cases(learned_arms):
    return ({(arm, seed, scenario) for arm in learned_arms for seed in SEEDS for scenario in SCENARIOS}
            | {('fixed', action, scenario) for action in range(3) for scenario in SCENARIOS}
            | {('hold', None, scenario) for scenario in SCENARIOS})


def validate_row(row):
    key = case_key(row)
    if row.get('window') != 'validation_2024' or row.get('trading_enabled') is not False:
        raise ValueError('Only disabled, previously seen 2024 exploration is allowed')
    metrics = row.get('metrics')
    if not isinstance(metrics, dict):
        raise ValueError('Missing metrics')
    for name in DECIMAL_METRICS:
        finite_decimal(metrics.get(name), name)
    for name, value in metrics.items():
        if value is None and name in ('win_rate', 'profit_factor'):
            continue  # Undefined hold statistics are explicit, not zero-filled.
        finite_decimal(value, name)
    for name in COUNT_METRICS:
        if type(metrics.get(name)) is not int or metrics[name] < 0:
            raise ValueError(f'Invalid count: {name}')
    if (not -1 < Decimal(metrics['total_return']) or not 0 <= Decimal(metrics['max_drawdown']) <= 1
            or Decimal(metrics['final_equity']) <= 0 or Decimal(metrics['open_ounces']) < 0
            or any(Decimal(metrics[k]) < 0 for k in ('fees', 'spread_cost', 'slippage_cost'))):
        raise ValueError('Invalid financial metric bounds')
    fills, equity = row.get('fills'), row.get('equity')
    if (not isinstance(fills, list) or len(fills) != metrics['fill_count']
            or not isinstance(equity, list) or not equity):
        raise ValueError('Missing or inconsistent complete receipts')
    previous = None
    for point in equity:
        at = point.get('at')
        if (type(at) is not int or (previous is not None and at <= previous)
                or finite_decimal(point.get('equity'), 'curve equity') <= 0):
            raise ValueError('Invalid ordered equity curve')
        previous = at
    if Decimal(equity[-1]['equity']) != Decimal(metrics['final_equity']):
        raise ValueError('Final equity disagrees with curve')
    if (type(row.get('effective_start')) is not int or type(row.get('effective_end')) is not int
            or not moment('2024-01-01') <= row['effective_start'] < row['effective_end'] <= moment('2025-01-01')
            or not row['effective_start'] < equity[0]['at'] <= equity[-1]['at'] == row['effective_end']):
        raise ValueError('Receipt window is not the frozen 2024 exploration')
    for fill in fills:
        if fill.get('side') not in ('BUY', 'SELL') or fill.get('simulated') is not True:
            raise ValueError('Invalid simulated fill')
        at, executed = fill.get('decision_at'), fill.get('execution_at')
        if (type(at) is not int or type(executed) is not int
                or not row['effective_start'] <= at <= executed <= at+120000
                or executed >= moment('2025-01-01')):
            raise ValueError('Invalid paired-quote execution time')
        for name in ('bid', 'ask', 'price', 'ounces', 'fee', 'cash_after', 'quantity_after'):
            number = finite_decimal(fill.get(name), name)
            if number < 0 or (name in ('bid', 'ask', 'price', 'ounces') and number == 0):
                raise ValueError('Invalid fill bounds')
        if Decimal(fill['ask']) < Decimal(fill['bid']):
            raise ValueError('Invalid paired quote')
    if key[0] == 'hold':
        if row.get('action_counts') is not None:
            raise ValueError('Hold reference has no target-action counts')
    else:
        counts = row.get('action_counts')
        if (not isinstance(counts, list) or len(counts) != 3
                or any(type(c) is not int or c < 0 for c in counts)
                or sum(counts) != len(equity)):
            raise ValueError('Invalid action counts or incomplete dense curve')
        expected_parity = 0 if key[0] == 'fixed' else sum(counts)
        if type(row.get('json_actor_parity_steps')) is not int or row['json_actor_parity_steps'] != expected_parity:
            raise ValueError('Missing stepwise reloaded PPO / serialized actor parity')
    explicit = sum((Decimal(metrics[k]) for k in ('fees', 'spread_cost', 'slippage_cost')), Decimal(0))
    if 'total_explicit_cost' in row and finite_decimal(row['total_explicit_cost'], 'explicit cost') != explicit:
        raise ValueError('Inconsistent explicit cost total')
    return key


def index_cases(rows, learned_arms):
    indexed = {}
    for row in rows:
        key = validate_row(row)
        if key in indexed:
            raise ValueError('Duplicate predeclared experiment case')
        indexed[key] = row
    if set(indexed) != expected_cases(learned_arms):
        raise ValueError('Missing or unexpected predeclared experiment cases')
    return indexed


def load_baseline(directory, protocol):
    directory = Path(directory)
    loaded = {}
    for name, expected in protocol['baseline_sha256'].items():
        raw = (directory/name).read_bytes()
        if digest(raw) != expected:
            raise ValueError(f'Frozen baseline SHA mismatch: {name}')
        loaded[name] = read_json(raw)
    baseline, study = loaded['protocol.json'], loaded['study.json']
    if (study.get('protocol') != baseline or baseline['version'] != VERSION
            or baseline['dataset'] != protocol['dataset']
            or baseline['train_start'] != moment(protocol['train_start'])
            or baseline['train_end'] != moment(protocol['train_end'])
            or baseline['validation_end'] != moment(protocol['exploratory_end'])
            or baseline['seeds'] != protocol['seeds']
            or baseline['timesteps_per_run'] != protocol['timesteps_per_run']
            or baseline['hyperparameters'] != protocol['hyperparameters']
            or baseline['code_sha256'] != protocol['frozen_code_sha256']
            or baseline['scaler'] != loaded['scaler.json']
            or Costs.model_validate(baseline['costs']) != Costs.model_validate(protocol['configured_costs'])
            or study.get('trading_enabled') is not False):
        raise ValueError('Frozen baseline contract mismatch')
    scaler = FrozenScaler.from_payload(loaded['scaler.json'])
    # Ignore original A rows, but require the full original 26-case inventory first.
    original = study.get('results')
    if not isinstance(original, list) or len(original) != 26:
        raise ValueError('Missing original frozen result inventory')
    a_cases = [(r.get('seed'), r.get('scenario')) for r in original if r.get('arm') == 'A']
    if len(a_cases) != 6 or set(a_cases) != {(s, c) for s in SEEDS for c in SCENARIOS}:
        raise ValueError('Invalid original A inventory')
    controls = [deepcopy(row) for row in original if row.get('arm') != 'A']
    index_cases(controls, ('B', 'C'))
    for row in controls:
        row['provenance'] = {'origin': 'frozen_v2_result',
            'study_sha256': protocol['baseline_sha256']['study.json'],
            'original_row_sha256': digest(canonical(row)), 'recomputed': False}
    return baseline, scaler, controls


def verify_environment(protocol, baseline, root=ROOT):
    for name, expected in protocol['frozen_code_sha256'].items():
        if digest((root/name).read_bytes()) != expected:
            raise ValueError(f'Frozen source SHA mismatch: {name}')
    dependencies = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'])
    if digest(dependencies) != protocol['dependencies_sha256']:
        raise ValueError('Frozen dependencies SHA mismatch; do not upgrade or silently change the environment')
    runtime = {name: importlib.metadata.version(name) for name in baseline['runtime']}
    if runtime != baseline['runtime'] or platform.python_version() != baseline['python']:
        raise ValueError('Frozen Python/runtime contract mismatch')
    return dependencies, runtime


def control_actor_origins(directory):
    directory = Path(directory)
    raw = (directory/'manifest.json').read_bytes()
    if digest(raw) != CONTROL_MANIFEST_SHA256:
        raise ValueError('Original published control manifest SHA mismatch')
    manifest, actors = read_json(raw), {}
    for arm in ARMS.values():
        for seed in SEEDS:
            name = f'actor-{arm}-seed-{seed}.json'
            actual = digest((directory/name).read_bytes())
            if actual != manifest.get(name):
                raise ValueError(f'Frozen control actor SHA mismatch: {name}')
            actors[name] = actual
    return {'original_manifest_sha256': CONTROL_MANIFEST_SHA256,
        'actor_sha256': actors, 'loaded_pickle_or_executed_controls': False,
        'new_model_deployment_evidence': False}


def prepare_data(history, protocol, baseline, scaler):
    data, all_bars = Catalogue(history).load(protocol['dataset'])
    start, split, end = map(moment, (protocol['train_start'], protocol['train_end'], protocol['exploratory_end']))
    if data['start'] > start or data['end'] < end:
        raise ValueError('Full predeclared 2023/2024 bounds required')
    # Full immutable snapshot is verified by Catalogue; never compute 2025 features.
    bars = tuple(b for b in all_bars if start <= b.start and b.start+HOUR <= end)
    train_bars = tuple(b for b in bars if b.start+HOUR <= split)
    if (len(train_bars) != baseline['train_bars']
            or len(bars)-len(train_bars) != baseline['validation_bars']):
        raise ValueError('Frozen train/exploration bar inventory mismatch')
    if canonical(FrozenScaler.fit(train_bars, start, split).payload()) != canonical(scaler.payload()):
        raise ValueError('Frozen 2023 scaler does not match actual training prefix')
    raw_train, raw_validation = market_features(train_bars), market_features(bars)
    np.testing.assert_array_equal(raw_train, raw_validation[:len(train_bars)])
    transforms, proof = {}, {}
    for native in ARMS.values():
        training = transform_market(raw_train, native, scaler)
        validation = transform_market(raw_validation, native, scaler)
        np.testing.assert_array_equal(training, validation[:len(train_bars)])
        transforms[native] = training, validation
        proof[native] = {'training_shape': list(training.shape), 'dtype': str(training.dtype),
            'training_sha256': digest(training.tobytes()),
            'exploration_prefix_sha256': digest(validation[:len(train_bars)].tobytes()), 'equal': True}
    opens = {r[0]: (r[1], Decimal(r[2]), Decimal(r[3])) for r in data['opens'] if start <= r[0] < end}
    return {'bars': bars, 'train_bars': train_bars, 'opens': opens,
        'train_opens': {at: quote for at, quote in opens.items() if at < split},
        'raw_validation': raw_validation, 'transforms': transforms,
        'prefix_proof': {'raw_equal': True, 'raw_training_sha256': digest(raw_train.tobytes()),
            'raw_exploration_prefix_sha256': digest(raw_validation[:len(train_bars)].tobytes()),
            'transforms': proof, 'scaler_sha256': digest(canonical(scaler.payload()))}}


def model_identity(arm, seed, actor_path, model_path, protocol, protocol_sha):
    actor = read_json(actor_path.read_bytes())
    if (protocol_sha != PROTOCOL_SHA256 or arm not in ARMS or type(seed) is not int or seed not in SEEDS
            or actor.get('arm') != ARMS[arm] or actor.get('version') != VERSION
            or actor.get('dataset') != protocol['dataset'] or actor.get('seed') != seed
            or actor.get('timesteps') != protocol['timesteps_per_run']
            or actor.get('train_end') != moment(protocol['train_end'])):
        raise ValueError('New research identity does not match its native actor')
    return {'study_version': STUDY_VERSION, 'study_arm': arm, 'native_input_arm': ARMS[arm],
        'seed': seed, 'dataset': protocol['dataset'], 'protocol_sha256': protocol_sha,
        'training_costs': Costs.model_validate(protocol['training_costs']).model_dump(mode='json'),
        'evaluation_costs': {name: costs.model_dump(mode='json') for name, costs in evaluation_costs(protocol['configured_costs'])},
        'timesteps': protocol['timesteps_per_run'], 'actor_file': actor_path.name,
        'actor_sha256': digest(actor_path.read_bytes()), 'ppo_file': model_path.name,
        'ppo_sha256': digest(model_path.read_bytes()),
        'deployment_evidence_inherited': False, 'promotion_authorized': False, 'live_trading': False}


def interpret(rows):
    """Every case is mandatory; averages cannot hide a failed seed or cash collapse."""
    indexed = index_cases(rows, ('B', 'C', 'D', 'E'))
    def metric(arm, seed, scenario, name):
        return Decimal(indexed[arm, seed, scenario]['metrics'][name])
    def activity(row):
        return {'at_least_two_actions': sum(c > 0 for c in row['action_counts']) >= 2,
                'has_fills': row['metrics']['fill_count'] > 0}
    h3 = []
    for seed in SEEDS:
        new, control = indexed['D', seed, SCENARIOS[1]], indexed['B', seed, SCENARIOS[1]]
        costs = lambda row: sum((Decimal(row['metrics'][k]) for k in ('fees', 'spread_cost', 'slippage_cost')), Decimal(0))
        checks = {'explicit_cost_lt_control': costs(new) < costs(control),
                  'return_gt_control': Decimal(new['metrics']['total_return']) > Decimal(control['metrics']['total_return']),
                  **activity(new)}
        h3.append({'seed': seed, 'scenario': SCENARIOS[1], 'checks': checks,
                   'new_explicit_cost': str(costs(new)), 'control_explicit_cost': str(costs(control)),
                   'return_delta': str(Decimal(new['metrics']['total_return'])-Decimal(control['metrics']['total_return']))})
    market, legacy = [], []
    for scenario in SCENARIOS:
        best = max((indexed['fixed', a, scenario] for a in range(3)),
                   key=lambda row: Decimal(row['metrics']['total_return']))
        for seed in SEEDS:
            row = indexed['D', seed, scenario]
            checks = {'return_gt_E': metric('D', seed, scenario, 'total_return') > metric('E', seed, scenario, 'total_return'),
                'return_gt_best_fixed': Decimal(row['metrics']['total_return']) > Decimal(best['metrics']['total_return']),
                'drawdown_le_E': metric('D', seed, scenario, 'max_drawdown') <= metric('E', seed, scenario, 'max_drawdown'),
                'drawdown_le_best_fixed': Decimal(row['metrics']['max_drawdown']) <= Decimal(best['metrics']['max_drawdown'])}
            market.append({'seed': seed, 'scenario': scenario, 'best_fixed_action': best['fixed_action'], 'checks': checks})
            legacy_checks = {'positive_return': Decimal(row['metrics']['total_return']) > 0,
                'drawdown_le_hold': Decimal(row['metrics']['max_drawdown']) <= metric('hold', None, scenario, 'max_drawdown'),
                **activity(row)}
            legacy.append({'arm': 'D', 'seed': seed, 'scenario': scenario, 'checks': legacy_checks,
                'exploratory_conditions_met': all(legacy_checks.values()), 'admission_authorized': False})
    return {'H3': {'supported': all(all(r['checks'].values()) for r in h3), 'all_seeds_required': True, 'cases': h3},
        'market_increment': {'demonstrated': all(all(r['checks'].values()) for r in market), 'cases': market},
        'legacy_gate_diagnostics': legacy, 'not_blind': True,
        'promotion_authorized': False, 'live_trading': False}


def run_study(*, history, baseline_directory, protocol_path, output):
    protocol, protocol_raw = load_protocol(protocol_path)
    baseline, scaler, results = load_baseline(baseline_directory, protocol)
    dependencies, runtime = verify_environment(protocol, baseline)
    origins = control_actor_origins(baseline_directory)
    data = prepare_data(history, protocol, baseline, scaler)
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)  # A partial or completed run cannot be resumed/overwritten.
    with (directory/'protocol.json').open('xb') as handle:
        handle.write(protocol_raw)
    with (directory/'dependencies.txt').open('xb') as handle:
        handle.write(dependencies)
    protocol_sha = digest(protocol_raw)
    run = {'study_version': STUDY_VERSION, 'frozen_at': datetime.now(timezone.utc).isoformat(),
        'protocol_sha256': protocol_sha, 'source_commit': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'runner_sha256': digest(Path(__file__).read_bytes()),
        'frozen_code_sha256': protocol['frozen_code_sha256'], 'runtime': runtime,
        'python': platform.python_version(), 'dependencies_sha256': digest(dependencies),
        'baseline_sha256': protocol['baseline_sha256'], 'control_actor_origins': origins,
        'prefix_proof': data['prefix_proof'],
        'training_costs': protocol['training_costs'], 'configured_evaluation_costs': protocol['configured_costs'],
        'promotion_authorized': False, 'live_trading': False}
    write_json(directory/'run.json', run)
    write_json(directory/'scaler.json', scaler.payload())
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    start, split, end = map(moment, (protocol['train_start'], protocol['train_end'], protocol['exploratory_end']))
    training_costs = Costs.model_validate(protocol['training_costs'])
    scenarios = evaluation_costs(protocol['configured_costs'])
    started, identities = time.monotonic(), []
    for arm, native in ARMS.items():
        training_features, validation_features = data['transforms'][native]
        for seed in SEEDS:
            env = GoldEnv(data['train_bars'], data['train_opens'], start, split,
                          training_costs.model_dump(mode='json'), features=training_features, receipts=False)
            check_env(env, warn=True)
            model = PPO('MlpPolicy', env, seed=seed, device='cpu', verbose=0, **HYPERPARAMETERS)
            model.learn(total_timesteps=protocol['timesteps_per_run'])
            if model.num_timesteps != protocol['timesteps_per_run']:
                raise ArithmeticError('Unexpected actual training budget')
            model_path = directory/f'ppo-{arm}-seed-{seed}.zip'
            model.save(model_path)
            del model
            model = PPO.load(model_path, device='cpu')  # Only the zip just generated above.
            actor = {**export_actor(model), 'version': VERSION, 'arm': native,
                'input': 'raw_v1_observation', 'scaler': scaler.payload(),
                'train_end': split, 'dataset': protocol['dataset'], 'seed': seed,
                'timesteps': protocol['timesteps_per_run']}
            actor['weights_sha256'] = digest(canonical(actor['layers']))
            actor_path = directory/f'actor-{arm}-seed-{seed}.json'
            write_json(actor_path, actor)
            actor = read_json(actor_path.read_bytes())
            identity = model_identity(arm, seed, actor_path, model_path, protocol, protocol_sha)
            identity_path = directory/f'model-{arm}-seed-{seed}.json'
            write_json(identity_path, identity)
            identities.append(identity)
            for scenario, costs in scenarios:
                evaluated = evaluate(GoldEnv(data['bars'], data['opens'], split, end,
                    costs.model_dump(mode='json'), features=validation_features),
                    model=model, actor=actor, raw_features=data['raw_validation'])
                evaluated.update(arm=arm, seed=seed, scenario=scenario, window='validation_2024',
                    provenance={'origin': 'new_training', 'model_manifest': identity_path.name,
                        'model_manifest_sha256': digest(identity_path.read_bytes()),
                        'protocol_sha256': protocol_sha, 'native_input_arm': native})
                validate_row(evaluated)
                write_json(directory/f'result-{arm}-{seed}-{scenario}.json', evaluated)
                results.append(evaluated)
            print(json.dumps({'arm': arm, 'native_input_arm': native, 'seed': seed,
                'steps': model.num_timesteps, 'elapsed_seconds': round(time.monotonic()-started, 2),
                'promotion_authorized': False, 'live_trading': False}), flush=True)
    # Keep each reference's complete original receipts; never retrain or rerun controls.
    with localcontext() as precision:
        precision.prec = 60
        for row in results:
            row['total_explicit_cost'] = str(sum((Decimal(row['metrics'][k]) for k in
                ('fees', 'spread_cost', 'slippage_cost')), Decimal(0)))
            fixed = [r for r in results if r['arm'] == 'fixed' and r['scenario'] == row['scenario']]
            row['excess_return_vs_fixed'] = {str(r['fixed_action']): str(
                Decimal(row['metrics']['total_return'])-Decimal(r['metrics']['total_return'])) for r in fixed}
    conclusion = interpret(results)
    report = {'study_version': STUDY_VERSION, 'protocol': protocol, 'run': run,
        'model_identities': identities, 'results': results, 'conclusion': conclusion,
        'elapsed_seconds': round(time.monotonic()-started, 2),
        'limitations': [
            '2024 is already viewed exploration, not blind or forward confirmation; no new 2025 computation.',
            'Six fixed-budget runs do not establish future profitability or statistical significance.',
            'Cost intervention includes native scenario-adjusted marks and endogenous account state.',
            'Commission/slippage are assumptions; no financing, depth, latency or broker specification guarantee.',
            'HistData historical and Swissquote live sources differ; source transfer is unverified.',
            'New actors inherit no original model f64/container/forward qualification.',
            'All seeds, failures and controls are retained; there is no winner selection or promotion.'],
        'promotion_authorized': False, 'live_trading': False, 'trading_enabled': False}
    write_json(directory/'study.json', report)
    write_json(directory/'summary.json', {**report, 'results': [
        {k: v for k, v in row.items() if k not in ('fills', 'equity')} for row in results]})
    write_json(directory/'manifest.json', {'study_version': STUDY_VERSION, 'protocol_sha256': protocol_sha,
        'model_identities': identities, 'artifacts_sha256': {
            p.name: digest(p.read_bytes()) for p in sorted(directory.iterdir()) if p.is_file()},
        'promotion_authorized': False, 'live_trading': False})
    print(json.dumps({'cases': len(results), 'H3_supported': conclusion['H3']['supported'],
        'market_increment_demonstrated': conclusion['market_increment']['demonstrated'],
        'promotion_authorized': False, 'live_trading': False}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--protocol', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    run_study(history=args.history, baseline_directory=args.baseline,
              protocol_path=args.protocol, output=args.output)


if __name__ == '__main__':
    main()
