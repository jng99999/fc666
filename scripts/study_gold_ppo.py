"""Predeclared multi-seed PPO research. Optional local dependencies; never submits orders."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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

from core.gold.backtest import Costs, simulate, CODE_SHA256
from core.gold.history import Catalogue, canonical, sha
from core.gold.rl import GoldEnv, FEATURES, VERSION, market_features
from core.strategy.registry import DEFINITIONS

SEEDS = (7, 19, 42)
HYPERPARAMETERS = {'n_steps': 512, 'batch_size': 64, 'n_epochs': 5, 'learning_rate': .0003,
                   'gamma': .99, 'gae_lambda': .95, 'clip_range': .2, 'ent_coef': .01,
                   'policy_kwargs': {'net_arch': [32, 32]}}


def moment(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000)


def export_actor(model):
    """Only locally trained weights, plain JSON. No pickle accepted by a service."""
    layers = []
    for layer in [*model.policy.mlp_extractor.policy_net, model.policy.action_net]:
        if isinstance(layer, torch.nn.Linear):
            layers.append({'kind': 'linear', 'weight': layer.weight.detach().cpu().numpy().tolist(),
                           'bias': layer.bias.detach().cpu().numpy().tolist()})
        elif isinstance(layer, torch.nn.Tanh):
            layers.append({'kind': 'tanh'})
        else:
            raise ValueError('Unsupported PPO actor layer')
    return {'version': VERSION, 'features': list(FEATURES), 'actions': ['0', '.25', '.5'], 'layers': layers}


def actor_action(actor, observation):
    if (actor.get('version') != VERSION or actor.get('features') != list(FEATURES)
            or actor.get('actions') != ['0', '.25', '.5']):
        raise ValueError('Gold PPO actor contract mismatch')
    values = np.asarray(observation, dtype=np.float32)
    if values.shape != (len(FEATURES),) or not np.isfinite(values).all():
        raise ValueError('Invalid gold PPO observation')
    for layer in actor['layers']:
        if layer['kind'] == 'linear':
            weight, bias = np.asarray(layer['weight'], dtype=np.float32), np.asarray(layer['bias'], dtype=np.float32)
            if weight.ndim != 2 or bias.shape != (weight.shape[0],) or weight.shape[1] != len(values):
                raise ValueError('Invalid gold PPO actor dimensions')
            values = weight@values+bias
        elif layer['kind'] == 'tanh':
            values = np.tanh(values)
        else:
            raise ValueError('Invalid gold PPO actor operation')
        if not np.isfinite(values).all():
            raise ValueError('Non-finite gold PPO actor weights/output')
    if values.shape != (3,):
        raise ValueError('Gold PPO actor must produce three action logits')
    return int(np.argmax(values))


def evaluate(model, actor, env):
    observation, _ = env.reset()
    done = False
    while not done:
        action, _ = model.predict(observation, deterministic=True)
        if int(action) != actor_action(actor, observation):
            raise ArithmeticError('JSON actor disagrees with frozen PPO model')
        observation, _, terminated, truncated, _ = env.step(int(action))
        done = terminated or truncated
    return env.result()


def admission(results):
    """No winner selection and no promotion endpoint. Every seed must qualify."""
    reasons = []
    def valid_metrics(row, policy):
        try:
            metrics = row['metrics']
            gain, drawdown = (Decimal(metrics[key]) for key in ('total_return', 'max_drawdown'))
            fills = metrics['fill_count']
            if not (gain.is_finite() and drawdown.is_finite() and 0 <= drawdown <= 1
                    and type(fills) is int and fills >= 0):
                return False
            if policy:
                counts = row['action_counts']
                return (isinstance(counts, list) and len(counts)==3
                        and all(type(c) is int and c >= 0 for c in counts) and sum(counts)>0)
            return True
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return False
    expected = {(seed, scenario) for seed in SEEDS for scenario in ('configured_costs', 'double_cost_stress')}
    actual = [(r['seed'], r['scenario']) for r in results if r['strategy']=='ppo_target_v1' and r['window']=='evaluation']
    if len(actual) != len(expected) or set(actual) != expected:
        reasons.append('评估项缺失、重复，或包含协议外的随机种子/成本场景')
    references = [r for r in results if r['strategy']=='buy_hold_reference' and r['window']=='evaluation']
    if (len(references) != 2 or {r['scenario'] for r in references} != {'configured_costs', 'double_cost_stress'}):
        reasons.append('评估用持有基准缺失或重复')
        return {'eligible': False, 'reasons': reasons, 'live_trading': False}
    if not all(valid_metrics(row, False) for row in references):
        return {'eligible': False, 'reasons': reasons+['持有基准指标非法或包含非有限数值'], 'live_trading': False}
    reference = {(r['window'], r['scenario']): r for r in references}
    for row in results:
        if row['strategy'] != 'ppo_target_v1' or row['window'] != 'evaluation':
            continue
        scenario_label = {'configured_costs': '配置成本', 'double_cost_stress': '双倍成本压力'}.get(row['scenario'], row['scenario'])
        label = f"种子 {row['seed']} · {scenario_label}"
        m = row['metrics']
        if not valid_metrics(row, True):
            reasons.append(f'{label}：指标或动作计数非法，拒绝准入')
            continue
        if row['scenario'] not in ('configured_costs', 'double_cost_stress'):
            continue
        if Decimal(m['total_return']) <= 0:
            reasons.append(f'{label}：扣费净收益不为正')
        if Decimal(m['max_drawdown']) > Decimal(reference[('evaluation', row['scenario'])]['metrics']['max_drawdown']):
            reasons.append(f'{label}：回撤超过同成本持有基准')
        if sum(count > 0 for count in row['action_counts']) < 2 or m['fill_count'] == 0:
            reasons.append(f'{label}：策略退化为固定动作或没有成交')
    return {'eligible': not reasons, 'reasons': reasons, 'live_trading': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--timesteps', type=int, default=131072)
    parser.add_argument('--commission', required=True)
    parser.add_argument('--slippage', required=True)
    args = parser.parse_args()
    if not 4096 <= args.timesteps <= 524288 or args.timesteps % 512:
        raise ValueError('Use 4096..524288 timesteps, multiple of 512')
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=False)  # Never overwrite an earlier experiment.
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    costs = Costs(initial_cash='10000', allocation='.5', commission=args.commission,
                  slippage=args.slippage, spread_multiplier='1')
    data, bars = Catalogue(args.history).load(args.dataset)
    bounds = [moment(d) for d in ('2023-01-01', '2024-01-01', '2025-01-01', '2026-01-01')]
    if data['start'] > bounds[0] or data['end'] < bounds[-1]:
        raise ValueError('Predeclared 2023..2025 split unavailable; do not silently shrink it')
    opens = {r[0]: (r[1], Decimal(r[2]), Decimal(r[3])) for r in data['opens']}
    features = market_features(bars)
    code = {**CODE_SHA256, 'gold/rl.py': hashlib.sha256(Path('core/gold/rl.py').read_bytes()).hexdigest(),
            'scripts/study_gold_ppo.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    protocol = {'train_start': '2023-01-01', 'train_end': '2024-01-01', 'validation_end': '2025-01-01',
                'evaluation_end': '2026-01-01', 'seeds': list(SEEDS), 'timesteps': args.timesteps,
                'actions': ['0%', '25%', '50%'], 'costs': costs.model_dump(mode='json'), 'not_blind': True,
                'hyperparameters': HYPERPARAMETERS, 'features': list(FEATURES),
                'selection': 'No tuning or seed selection using validation/evaluation results',
                'reward': '100 * log(next observed marked equity / previous marked equity), net of costs',
                'gate_policy': 'Every seed: positive evaluation return under both costs, drawdown <= same-cost hold, >=2 actions and fills',
                'runtime': {name: importlib.metadata.version(name) for name in ('stable-baselines3', 'torch', 'gymnasium', 'numpy')},
                'python': platform.python_version(), 'code_sha256': code}
    (directory/'protocol.json').write_bytes(canonical(protocol))
    started, results, artifacts = time.monotonic(), [], {}
    windows = [('validation', bounds[1], bounds[2]), ('evaluation', bounds[2], bounds[3])]
    scenarios = [('configured_costs', costs), ('double_cost_stress', costs.model_copy(update={
        'commission': costs.commission*2, 'slippage': costs.slippage*2, 'spread_multiplier': Decimal(2)}))]
    for seed in SEEDS:
        # Pass only the training prefix, not a full dataset sliced by a wrapper.
        train_bars = tuple(b for b in bars if b.start+3600000 <= bounds[1])
        train_opens = {at: value for at, value in opens.items() if at < bounds[1]}
        env = GoldEnv(train_bars, train_opens, bounds[0], bounds[1], costs.model_dump(mode='json'), receipts=False)
        check_env(env, warn=True)
        model = PPO('MlpPolicy', env, seed=seed, device='cpu', verbose=0, **HYPERPARAMETERS)
        model.learn(total_timesteps=args.timesteps)
        path = directory/f'ppo-seed-{seed}.zip'
        model.save(path)
        del model
        model = PPO.load(path, device='cpu')  # Our own locally written artifact only.
        actor = export_actor(model)
        actor_file = directory/f'actor-seed-{seed}.json'
        actor_file.write_bytes(canonical(actor))
        artifacts[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        artifacts[actor_file.name] = hashlib.sha256(actor_file.read_bytes()).hexdigest()
        for window, a, b in windows:
            for scenario, configured in scenarios:
                evaluated = evaluate(model, actor, GoldEnv(bars, opens, a, b, configured.model_dump(mode='json'), features=features))
                evaluated.update(strategy='ppo_target_v1', seed=seed, window=window, scenario=scenario)
                results.append(evaluated)
        print(json.dumps({'seed': seed, 'cases': len(results), 'seconds': round(time.monotonic()-started, 2)}), flush=True)
    for name in [*DEFINITIONS, 'buy_hold_reference', 'cash_reference']:
        for window, a, b in windows:
            for scenario, configured in scenarios:
                if name == 'cash_reference':
                    result = {'metrics': {'total_return': '0', 'max_drawdown': '0', 'fees': '0', 'spread_cost': '0',
                                          'slippage_cost': '0', 'fill_count': 0}, 'fills': [], 'equity': []}
                else:
                    params = {} if name == 'buy_hold_reference' else DEFINITIONS[name].create({})[1].model_dump(mode='json')
                    result = simulate(bars, 1, a, b, configured, {'strategy': name, 'parameters': params}, 100, opens)
                result.update(strategy=name, seed=None, window=window, scenario=scenario, action_counts=None)
                results.append(result)
    report = {'version': VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(),
              'dataset': {'id': args.dataset, 'source': data['source'], 'start': '2023-01-01', 'end': '2026-01-01',
                          'bars': len(bars)}, 'protocol': protocol, 'gate': admission(results), 'results': results,
              'artifacts_sha256': artifacts, 'limitations': [
                  '2025 年是按时间分离的后续评估；该历史曾被此前研究查看，因此不属于密封盲测。',
                  '三种子和固定训练预算属于初步实验，不能证明未来盈利或模型稳定有效。',
                  'HistData 含休市与未知缺口；不生成虚构平柱，不用未来值填补特征。',
                  '手续费和额外滑点是显式假设，未计隔夜费、融资与真实可成交深度。',
                  'PPO 目标仓位会再平衡，八个规则基线只做多/空仓进出，因此换手机制不同。',
                  '历史 HistData 与实时 Swissquote 行情源不同，不保证经纪商成交表现。',
                  '测试站只展示固定结果，不运行模型推理、训练、账户修改或策略启用。'],
              'trading_enabled': False}
    (directory/'study.json').write_bytes(canonical(report))
    summary = {**report, 'results': [{k: v for k, v in r.items() if k not in ('fills', 'equity')} for r in results]}
    (directory/'summary.json').write_bytes(canonical(summary))
    with (directory/'dependencies.txt').open('w') as handle:
        subprocess.run([sys.executable, '-m', 'pip', 'freeze'], stdout=handle, check=True)
    print(json.dumps({'cases': len(results), 'gate': report['gate'], 'seconds': round(time.monotonic()-started, 2),
                      'study_sha256': hashlib.sha256((directory/'study.json').read_bytes()).hexdigest()}), flush=True)


if __name__ == '__main__':
    main()
