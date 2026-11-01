"""One frozen A/B/C run, all seeds, 2024 validation only. No tuning or live orders."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
import time

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.env_checker import check_env
import torch

from core.gold.backtest import Costs, simulate
from core.gold.history import Catalogue, HOUR, canonical
from core.gold.rl import FEATURES, GoldEnv, market_features
from core.gold.rl_experiments import FrozenScaler, VERSION, actor_action, transform_market
from scripts.study_gold_ppo import HYPERPARAMETERS, SEEDS, export_actor, moment

DATASET = 'ee9afe981db266959ebcc1557e6120ae6e60012f761265ed46d57ab57878276c'
TIMESTEPS = 131072
SCENARIOS = ('configured_costs', 'double_cost_stress')


def write_json(path, payload):
    with path.open('xb') as handle:
        handle.write(canonical(payload))


def evaluate(env, *, model=None, actor=None, raw_features=None, fixed=None):
    observation, _ = env.reset()
    checks = 0
    while True:
        if fixed is not None:
            action = fixed
        else:
            predicted, _ = model.predict(observation, deterministic=True)
            raw = np.concatenate((raw_features[env.index], observation[7:]))
            action = actor_action(actor, raw)
            if int(predicted) != action:
                raise ArithmeticError('Saved JSON actor disagrees with saved PPO')
            checks += 1
        observation, _, terminated, truncated, _ = env.step(action)
        if terminated or truncated:
            result = env.result()
            result['json_actor_parity_steps'] = checks
            return result


def interpret(results):
    """Frozen preregistered criteria. All seeds/scenarios required; no winner picking."""
    indexed = {(r['arm'], r['seed'], r['scenario']): r for r in results if r['arm'] in ('A', 'B', 'C')}
    expected = {(arm, seed, scenario) for arm in ('A', 'B', 'C') for seed in SEEDS for scenario in SCENARIOS}
    if set(indexed) != expected or len([r for r in results if r['arm'] in ('A', 'B', 'C')]) != 18:
        raise ValueError('Missing/duplicate predeclared experiment cases')

    def metric(arm, seed, scenario, name):
        return Decimal(indexed[arm, seed, scenario]['metrics'][name])

    def second_share(row):
        counts = sorted(row['action_counts'], reverse=True)
        return Decimal(counts[1])/sum(counts)

    shares = {str(seed): str(second_share(indexed['B', seed, SCENARIOS[0]])) for seed in SEEDS}
    delta = [metric('B', s, SCENARIOS[0], 'total_return')-metric('A', s, SCENARIOS[0], 'total_return') for s in SEEDS]
    h1_checks = {
        'B_two_seeds_second_action_at_least_5pct': sum(Decimal(v) >= Decimal('.05') for v in shares.values()) >= 2,
        'median_B_minus_A_return_positive': median(delta) > 0,
        'median_B_drawdown_le_A': median(metric('B', s, SCENARIOS[0], 'max_drawdown') for s in SEEDS)
            <= median(metric('A', s, SCENARIOS[0], 'max_drawdown') for s in SEEDS),
        'median_B_stress_return_nonnegative': median(metric('B', s, SCENARIOS[1], 'total_return') for s in SEEDS) >= 0,
    }
    h2_rows, gates = [], []
    for scenario in SCENARIOS:
        references = [r for r in results if r['arm'] == 'fixed' and r['scenario'] == scenario]
        if len(references) != 3 or {r['fixed_action'] for r in references} != {0, 1, 2}:
            raise ValueError('Missing fixed exposure reference')
        best = max(references, key=lambda r: Decimal(r['metrics']['total_return']))
        hold = next(r for r in results if r['arm'] == 'hold' and r['scenario'] == scenario)
        for seed in SEEDS:
            b, c = indexed['B', seed, scenario], indexed['C', seed, scenario]
            checks = {
                'return_gt_C': Decimal(b['metrics']['total_return']) > Decimal(c['metrics']['total_return']),
                'return_gt_best_fixed': Decimal(b['metrics']['total_return']) > Decimal(best['metrics']['total_return']),
                'drawdown_le_C': Decimal(b['metrics']['max_drawdown']) <= Decimal(c['metrics']['max_drawdown']),
                'drawdown_le_best_fixed': Decimal(b['metrics']['max_drawdown']) <= Decimal(best['metrics']['max_drawdown']),
            }
            h2_rows.append({'seed': seed, 'scenario': scenario, 'best_fixed_action': best['fixed_action'], 'checks': checks})
            for arm in ('A', 'B', 'C'):
                row = indexed[arm, seed, scenario]
                legacy = {
                    'positive_return': Decimal(row['metrics']['total_return']) > 0,
                    'drawdown_le_hold': Decimal(row['metrics']['max_drawdown']) <= Decimal(hold['metrics']['max_drawdown']),
                    'at_least_two_actions': sum(c > 0 for c in row['action_counts']) >= 2,
                    'has_fills': row['metrics']['fill_count'] > 0,
                    'return_gt_best_fixed': Decimal(row['metrics']['total_return']) > Decimal(best['metrics']['total_return']),
                }
                gates.append({'arm': arm, 'seed': seed, 'scenario': scenario, 'checks': legacy,
                              'validation_only_qualifies': all(legacy.values())})
    return {'H1': {'supported': all(h1_checks.values()), 'checks': h1_checks,
                   'B_second_action_share': shares, 'median_return_delta': str(median(delta))},
            'H2_market_increment': {'demonstrated': all(all(r['checks'].values()) for r in h2_rows), 'cases': h2_rows},
            'validation_gate_diagnostics': gates, 'promotion_authorized': False,
            'not_blind': True, 'live_trading': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    costs = Costs(initial_cash='10000', allocation='.5', commission='.10', slippage='.05', spread_multiplier='1')
    data, all_bars = Catalogue(args.history).load(DATASET)
    start, split, end = map(moment, ('2023-01-01', '2024-01-01', '2025-01-01'))
    if data['start'] > start or data['end'] < end:
        raise ValueError('Full predeclared 2023/2024 bounds required')
    # The loader verifies the complete immutable snapshot; no 2025 features or outcomes are computed.
    bars = tuple(b for b in all_bars if b.start+HOUR <= end)
    train_bars = tuple(b for b in bars if b.start+HOUR <= split)
    opens = {r[0]: (r[1], Decimal(r[2]), Decimal(r[3])) for r in data['opens'] if r[0] < end}
    train_opens = {at: quote for at, quote in opens.items() if at < split}
    scaler = FrozenScaler.fit(train_bars, start, split)
    raw_train, raw_validation = market_features(train_bars), market_features(bars)
    # Real-snapshot prefix proof before training, not just a date filter around a full env.
    np.testing.assert_array_equal(raw_train, raw_validation[:len(train_bars)])
    for arm in ('A', 'B', 'C'):
        fitted = None if arm == 'A' else scaler
        np.testing.assert_array_equal(transform_market(raw_train, arm, fitted),
                                      transform_market(raw_validation, arm, fitted)[:len(train_bars)])
    tracked = ['core/gold/rl.py', 'core/gold/backtest.py', 'core/gold/history.py',
               'scripts/study_gold_ppo.py', 'scripts/study_gold_ppo_v2.py',
               'core/gold/rl_experiments/__init__.py']
    root = Path(__file__).resolve().parents[1]
    protocol = {
        'version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(), 'dataset': DATASET,
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'train_start': start, 'train_end': split, 'validation_end': end, 'not_blind': True,
        'arms': {'A': 'original raw v1', 'B': '2023-fitted frozen mean/std', 'C': 'B with seven market inputs zeroed'},
        'seeds': list(SEEDS), 'timesteps_per_run': TIMESTEPS, 'runs': 9,
        'hyperparameters': HYPERPARAMETERS, 'costs': costs.model_dump(mode='json'),
        'stress': 'commission, slippage and spread multiplier doubled; no stress retraining',
        'features': list(FEATURES), 'scaler': scaler.payload(),
        'H1': '>=2 B seeds second action share >=.05; median B-A return >0; median B DD<=A; median B stress return>=0',
        'H2': 'Every B seed under both costs beats paired C and best fixed return, DD<=C and that best fixed reference',
        'selection': 'No tuning, no selected seed, no 2025 evaluation, no promotion',
        'prefix_checks': 'raw and A/B/C transformed training prefix equal full-through-2024 prefix',
        'runtime': {n: importlib.metadata.version(n) for n in ('numpy', 'torch', 'gymnasium', 'stable-baselines3')},
        'python': platform.python_version(), 'code_sha256': {p: hashlib.sha256((root/p).read_bytes()).hexdigest() for p in tracked},
        'train_bars': len(train_bars), 'validation_bars': len(bars)-len(train_bars),
        'training_gap_transitions': sum(b.start-a.start != HOUR for a, b in zip(train_bars[99:-1], train_bars[100:])),
        'source_quality': data.get('quality'), 'trading_enabled': False,
    }
    write_json(directory/'protocol.json', protocol)
    write_json(directory/'scaler.json', scaler.payload())
    scenarios = [(SCENARIOS[0], costs), (SCENARIOS[1], costs.model_copy(update={
        'commission': costs.commission*2, 'slippage': costs.slippage*2, 'spread_multiplier': Decimal(2)}))]
    started, results = time.monotonic(), []
    for arm in ('A', 'B', 'C'):
        fitted = None if arm == 'A' else scaler
        train_features = transform_market(raw_train, arm, fitted)
        validation_features = transform_market(raw_validation, arm, fitted)
        for seed in SEEDS:
            env = GoldEnv(train_bars, train_opens, start, split, costs.model_dump(mode='json'), features=train_features, receipts=False)
            check_env(env, warn=True)
            model = PPO('MlpPolicy', env, seed=seed, device='cpu', verbose=0, **HYPERPARAMETERS)
            model.learn(total_timesteps=TIMESTEPS)
            path = directory/f'ppo-{arm}-seed-{seed}.zip'
            model.save(path)
            del model
            model = PPO.load(path, device='cpu')  # Only our just-generated artifact.
            actor = {**export_actor(model), 'version': VERSION, 'arm': arm, 'input': 'raw_v1_observation',
                     'scaler': None if fitted is None else fitted.payload(),
                     'train_end': split, 'dataset': DATASET, 'seed': seed,
                     'timesteps': TIMESTEPS}
            actor['weights_sha256'] = hashlib.sha256(canonical(actor['layers'])).hexdigest()
            actor_path = directory/f'actor-{arm}-seed-{seed}.json'
            write_json(actor_path, actor)
            actor = json.loads(actor_path.read_text())  # Check serialized, not in-memory export.
            for scenario, configured in scenarios:
                evaluated = evaluate(GoldEnv(bars, opens, split, end, configured.model_dump(mode='json'),
                                              features=validation_features), model=model, actor=actor, raw_features=raw_validation)
                evaluated.update(arm=arm, seed=seed, scenario=scenario, window='validation_2024')
                write_json(directory/f'result-{arm}-{seed}-{scenario}.json', evaluated)
                results.append(evaluated)
            print(json.dumps({'arm': arm, 'seed': seed, 'steps': model.num_timesteps,
                              'elapsed_seconds': round(time.monotonic()-started, 2)}), flush=True)
    for scenario, configured in scenarios:
        for action in range(3):
            result = evaluate(GoldEnv(bars, opens, split, end, configured.model_dump(mode='json'), features=raw_validation), fixed=action)
            result.update(arm='fixed', fixed_action=action, seed=None, scenario=scenario, window='validation_2024')
            results.append(result)
        hold = simulate(bars, 1, split, end, configured, {'strategy': 'buy_hold_reference', 'parameters': {}}, 100, opens)
        hold.update(arm='hold', seed=None, scenario=scenario, window='validation_2024', action_counts=None)
        results.append(hold)
    for row in results:
        fixed = [r for r in results if r['arm'] == 'fixed' and r['scenario'] == row['scenario']]
        row['excess_return_vs_fixed'] = {str(r['fixed_action']): str(Decimal(row['metrics']['total_return'])-Decimal(r['metrics']['total_return'])) for r in fixed}
        row['total_explicit_cost'] = str(sum(Decimal(row['metrics'][k]) for k in ('fees', 'spread_cost', 'slippage_cost')))
    conclusion = interpret(results)
    report = {'protocol': protocol, 'results': results, 'conclusion': conclusion,
              'elapsed_seconds': round(time.monotonic()-started, 2), 'trading_enabled': False,
              'limitations': [
                  '2024 validation and 2025 history have previously been viewed; neither is sealed blind evidence.',
                  '5038 training decisions repeated ~26 times per seed are not 131072 independent market samples.',
                  'HistData gaps include closures/unknown missing observations; no invented bars or forward fill.',
                  'Quotes are historical paired boundaries, not current executable broker quotes. Quote TTL is 120 seconds.',
                  'No financing/overnight costs, real depth, latency, partial fills or broker specifications modeled.',
                  'Only 3 seeds, 1 training year and 1 validation year; no statistical significance or forward profit claim.',
                  'C retains endogenous account state, which can indirectly encode past price moves; it is not an information-free policy.',
                  'All execution is simulated; no account integration or model promotion authorized.']}
    write_json(directory/'study.json', report)
    summary = {**report, 'results': [{k: v for k, v in r.items() if k not in ('fills', 'equity')} for r in results]}
    write_json(directory/'summary.json', summary)
    with (directory/'dependencies.txt').open('x') as handle:
        subprocess.run([sys.executable, '-m', 'pip', 'freeze'], stdout=handle, check=True)
    write_json(directory/'manifest.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir()) if p.is_file()})
    print(json.dumps({'cases': len(results), 'H1_supported': conclusion['H1']['supported'],
                      'market_increment_demonstrated': conclusion['H2_market_increment']['demonstrated'],
                      'seconds': report['elapsed_seconds'], 'trading_enabled': False}), flush=True)


if __name__ == '__main__':
    main()
