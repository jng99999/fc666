"""Supplemental numeric audit against the sibling paper runtime; never registers models.

Run after the frozen native experiment. This does not retrain or replace its protocol.
The controlled sibling source is read only; only its pure inference function is loaded.
"""
import argparse
import ast
from decimal import Decimal, localcontext
import hashlib
import json
import math
from pathlib import Path
import re
import time

from core.gold.history import Bar, Catalogue, HOUR, canonical, sha
from core.gold.rl import FEATURES, GoldEnv, market_features
from core.gold.rl_experiments import FrozenScaler, transform_market
from scripts.study_gold_ppo import moment
from scripts.study_gold_ppo_v2 import DATASET, SCENARIOS, interpret, write_json

VERSION = 'gold-policy-ablation-f64-v2'
MARGIN_THRESHOLD = 1e-6


def load_controlled_runtime(runtime_path):
    """Actual pure function, not an assumed reimplementation or an uploaded plugin."""
    source = runtime_path.read_bytes()
    tree = ast.parse(source)
    names = {'infer', 'preprocess', 'validate_actor', 'validate_preprocessor', 'observation_values', '_observation_values'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in nodes} != names:
        raise ValueError('Reviewed runtime helpers unavailable')
    namespace = {'FEATURES': FEATURES, 'TARGETS': ('0', '.25', '.5'), 'PROTOCOL': 'gold-policy-json-f64-v1',
                 'V2_PROTOCOL': VERSION, 'MIN_LOGIT_MARGIN': MARGIN_THRESHOLD,
                 'math': math, 'time': time, 'Rejected': ValueError, 're': re,
                 'digest': sha, 'Decimal': Decimal, 'localcontext': localcontext, 'HOUR': HOUR, 'Bar': Bar}
    # Check reviewed constants against actual sibling rather than silently patching its semantics.
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ('PROTOCOL', 'V2_PROTOCOL', 'MIN_LOGIT_MARGIN'):
                if ast.literal_eval(node.value) != namespace[name]:
                    raise ValueError(f'Runtime constant changed: {name}')
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, str(runtime_path), 'exec'), namespace)
    return namespace, hashlib.sha256(source).hexdigest(), hashlib.sha256(ast.dump(module).encode()).hexdigest()


class AuditEnv(GoldEnv):
    """A rejected prediction consumes time with no order, never target-zero fallback."""
    abstain_next = False

    def _execute(self, action, at):
        if self.abstain_next:
            self.abstain_next = False
            return
        super()._execute(action, at)

    def step_abstain(self):
        self.abstain_next = True
        transition = super().step(0)
        self.actions[0] -= 1  # No policy action occurred; report refusals separately.
        return transition


def f64_market_features(bars):
    """Pure Python f64 version of the sibling observe() market formulas, prefix only."""
    rows = [[0.]*7 for _ in bars]
    for i in range(20, len(bars)):
        close = float(bars[i].close)
        returns = [math.log(float(bars[j].close/bars[j-1].close)) for j in range(i-19, i+1)]
        mean = math.fsum(returns)/20
        values = [math.log(close/float(bars[i-n].close)) for n in (1, 4, 20)]
        values += [float((max(b.high for b in bars[i-19:i+1])-min(b.low for b in bars[i-19:i+1]))/bars[i].close),
                   math.sqrt(math.fsum((r-mean)**2 for r in returns)/20),
                   float((bars[i].ask_close-bars[i].bid_close)/bars[i].close),
                   math.log1p((bars[i].start-bars[i-1].start)/HOUR)]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nonfinite f64 market feature')
        rows[i] = [max(-10., min(10., v)) for v in values]
    return rows


def logits_and_margin(actor, observation):
    values = observation
    for layer in actor['layers']:
        if layer['kind'] == 'linear':
            values = [math.fsum(w*v for w, v in zip(weights, values, strict=True))+bias
                      for weights, bias in zip(layer['weight'], layer['bias'], strict=True)]
        else:
            values = [math.tanh(v) for v in values]
    ranked = sorted(values, reverse=True)
    return values, ranked[0]-ranked[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', required=True)
    parser.add_argument('--native', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--runtime-source', required=True, help='Reviewed operator-controlled policy_runtime.py, not an uploaded model')
    args = parser.parse_args()
    from stable_baselines3 import PPO
    import torch
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    native = Path(args.native)
    study = json.loads((native/'study.json').read_text())
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=False)
    runtime_path = Path(args.runtime_source)
    runtime, runtime_sha, infer_ast_sha = load_controlled_runtime(runtime_path)
    runtime_tree = ast.parse(runtime_path.read_text())
    runtime_nodes = [n for n in runtime_tree.body if isinstance(n, ast.FunctionDef) and n.name in
                     ('infer', 'preprocess', 'validate_actor', 'validate_preprocessor', 'observation_values', '_observation_values')]
    (directory/'runtime-infer-snapshot.py').write_text(ast.unparse(ast.Module(body=runtime_nodes, type_ignores=[]))+'\n')
    write_json(directory/'protocol.json', {
        'purpose': 'Supplemental f64 numeric compatibility audit, no retraining or promotion',
        'native_protocol_sha256': hashlib.sha256((native/'protocol.json').read_bytes()).hexdigest(),
        'runtime_source': str(runtime_path), 'runtime_source_sha256': runtime_sha,
        'audit_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'infer_ast_sha256': infer_ast_sha, 'arithmetic': 'Python f64, row-order math.fsum + bias, math.tanh, first argmax',
        'input': 'actual sibling observation_values then preprocess; frozen f32-trained 2023 scaler; own cash/quantity and bid-mark peak',
        'margin_threshold': MARGIN_THRESHOLD, 'margin_rule': 'actual runtime abstains without an order when margin <= threshold',
        'native_comparison': 'SB3 f32 policy on matching own f64-trajectory account state and original f32 market features',
        'no_retagging': True, 'format_supported': True, 'registered': False, 'trading_enabled': False,
    })
    (directory/'audit-code-snapshot.py').write_bytes(Path(__file__).read_bytes())
    data, all_bars = Catalogue(args.history).load(DATASET)
    split, end = moment('2024-01-01'), moment('2025-01-01')
    bars = tuple(b for b in all_bars if b.start+HOUR <= end)
    opens = {r[0]: (r[1], Decimal(r[2]), Decimal(r[3])) for r in data['opens'] if r[0] < end}
    f32 = market_features(bars)
    scaler = FrozenScaler.from_payload(study['protocol']['scaler'])
    results = []
    for arm in ('A', 'B', 'C'):
        for seed in (7, 19, 42):
            actor = json.loads((native/f'actor-{arm}-seed-{seed}.json').read_text())
            model = PPO.load(native/f'ppo-{arm}-seed-{seed}.zip', device='cpu')
            portable = {**actor, 'version': VERSION, 'arithmetic': 'python_f64_fsum_tanh_margin_1e-6',
                        'input': 'raw_f64_observation', 'min_logit_margin': MARGIN_THRESHOLD,
                        'native_actor_sha256': hashlib.sha256((native/f'actor-{arm}-seed-{seed}.json').read_bytes()).hexdigest()}
            runtime['validate_actor'](portable)
            write_json(directory/f'actor-{arm}-{seed}.json', portable)
            for scenario in SCENARIOS:
                cost = dict(study['protocol']['costs'])
                if scenario == 'double_cost_stress':
                    cost.update(commission='.20', slippage='.10', spread_multiplier='2')
                native_features = transform_market(f32, arm, None if arm == 'A' else scaler)
                env = AuditEnv(bars, opens, split, end, cost, features=native_features)
                observation, _ = env.reset()
                deployment_peak = cost['initial_cash']
                differences, numeric_differences, weak, checked, timeouts, abstentions = 0, 0, 0, 0, 0, 0
                minimum, differing_steps = math.inf, []
                while True:
                    values, deployment_peak = runtime['observation_values'](
                        bars[max(0, env.index-99):env.index+1], str(env.cash), str(env.quantity), deployment_peak)
                    logits, margin = logits_and_margin(portable, runtime['preprocess'](portable, values))
                    try:
                        action = runtime['infer'](portable, values, time.monotonic()+.1)
                    except ValueError as error:
                        if str(error) not in ('LOW_LOGIT_MARGIN', 'INFERENCE_TIMEOUT'):
                            raise
                        timeouts += str(error) == 'INFERENCE_TIMEOUT'
                        abstentions += 1
                        action = None
                    assert action is None or action == max(range(3), key=logits.__getitem__)
                    native_action, _ = model.predict(observation, deterministic=True)
                    numeric_differences += int(native_action) != max(range(3), key=logits.__getitem__)
                    if int(native_action) != action:
                        differences += 1
                        differing_steps.append({'index': env.index, 'native_action': int(native_action),
                                                'f64_action': action, 'margin': margin})
                    minimum = min(minimum, margin)
                    weak += margin <= MARGIN_THRESHOLD
                    checked += 1
                    observation, _, terminated, truncated, _ = env.step_abstain() if action is None else env.step(action)
                    if terminated or truncated:
                        break
                result = env.result()
                native_result = next(r for r in study['results'] if r['arm'] == arm and r['seed'] == seed and r['scenario'] == scenario)
                result.update(arm=arm, seed=seed, scenario=scenario, window='validation_2024',
                              native_action_differences=differences, low_margin_steps=weak,
                              minimum_logit_margin=minimum, inference_steps=checked,
                              numeric_argmax_differences=numeric_differences,
                              inference_timeouts=timeouts, abstention_count=abstentions,
                              differing_steps=differing_steps, native_metrics_equal=result['metrics'] == native_result['metrics'])
                result['candidate_eligible'] = False
                result['candidate_rejection_reasons'] = [
                    'HistData training/validation source differs from current Swissquote runtime source; no source transfer qualification.',
                    'No fresh forward qualification or trusted registration authorization.']
                if differences:
                    result['candidate_rejection_reasons'].append('f32/f64 deterministic actions differ on own trajectory.')
                if weak:
                    result['candidate_rejection_reasons'].append('At least one logit margin <= predeclared 1e-6 threshold.')
                if timeouts:
                    result['candidate_rejection_reasons'].append('Actual 100ms inference deadline refused predictions; no order was substituted.')
                results.append(result)
                write_json(directory/f'result-{arm}-{seed}-{scenario}.json', result)
                print(json.dumps({'arm': arm, 'seed': seed, 'scenario': scenario, 'differences': differences,
                                  'numeric_argmax_differences': numeric_differences, 'timeouts': timeouts,
                                  'low_margin_steps': weak, 'min_margin': minimum}), flush=True)
    results += [r for r in study['results'] if r['arm'] in ('fixed', 'hold')]
    for row in results:
        fixed = [r for r in results if r['arm'] == 'fixed' and r['scenario'] == row['scenario']]
        row['excess_return_vs_fixed'] = {str(r['fixed_action']): str(Decimal(row['metrics']['total_return'])-Decimal(r['metrics']['total_return'])) for r in fixed}
        row['total_explicit_cost'] = str(sum(Decimal(row['metrics'][k]) for k in ('fees', 'spread_cost', 'slippage_cost')))
    conclusion = (interpret(results) if not any(r.get('abstention_count', 0) for r in results)
                  else {'promotion_authorized': False, 'reason': 'Explicit abstentions; no dropping/refilling refused actions to force three-action gate.'})
    outcome = {'results': results, 'conclusion': conclusion, 'candidate_eligible': False,
               'trading_enabled': False, 'runtime_source_sha256': runtime_sha,
               'infer_ast_sha256': infer_ast_sha, 'cross_platform_bitwise_identity_claimed': False}
    write_json(directory/'study.json', outcome)
    write_json(directory/'summary.json', {**outcome, 'results': [{k: v for k, v in r.items() if k not in ('fills', 'equity', 'differing_steps')} for r in results]})
    write_json(directory/'manifest.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir()) if p.is_file()})


if __name__ == '__main__':
    main()
