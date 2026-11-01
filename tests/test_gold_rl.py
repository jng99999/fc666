"""Optional RL runtime tests. Prices here are synthetic fixtures, not market evidence."""
from dataclasses import replace
from decimal import Decimal, ROUND_FLOOR, localcontext

import pytest

pytest.importorskip('gymnasium', reason='Install the isolated requirements-rl.txt runtime to run RL tests')
import numpy as np
from gymnasium.utils.env_checker import check_env

from core.gold.history import Bar, HOUR
from core.gold.rl import GoldEnv, market_features


START = 1704067200000
COSTS = {'initial_cash': '10000', 'allocation': '.5', 'commission': '.10',
         'slippage': '.05', 'spread_multiplier': '1'}


def synthetic_bars(count=240):
    result = []
    for i in range(count):
        price = Decimal(2000)+Decimal(i % 43)/10-Decimal(i % 17)/20
        at = START+i*HOUR
        result.append(Bar(at, at+1000, at+HOUR-1000, price, price+Decimal('.4'),
                          price+Decimal('.1'), price+Decimal('.5'), price+2, price-1, 60, 60000))
    return result


def opening_quotes(bars):
    return {b.start: (b.first, b.bid_open, b.ask_open) for b in bars}


def environment(bars=None, *, opens=None, costs=None, start=None, end=None, **kwargs):
    rows = synthetic_bars() if bars is None else bars
    return GoldEnv(rows, opening_quotes(rows) if opens is None else opens,
                   START if start is None else start, rows[-1].start+HOUR if end is None else end,
                   COSTS if costs is None else costs, **kwargs)


def finish(env, policy=lambda i: (i // 7) % 3):
    observation, _ = env.reset(seed=17)
    count = 0
    while True:
        assert np.isfinite(observation).all()
        observation, reward, terminated, truncated, _ = env.step(policy(count))
        assert np.isfinite(reward)
        assert terminated is False
        assert env.cash >= 0 and env.quantity >= 0
        count += 1
        if truncated:
            break
    return env.result()


def test_prefix_observations_actions_and_receipts_ignore_future_suffix():
    rows = synthetic_bars()
    changed = rows[:170] + [replace(b, bid_open=b.bid_open*3, ask_open=b.ask_open*3,
                                  bid_close=b.bid_close*3, ask_close=b.ask_close*3,
                                  high=b.high*3, low=b.low*3) for b in rows[170:]]
    np.testing.assert_array_equal(market_features(rows)[:170], market_features(changed)[:170])
    left, right = environment(rows), environment(changed)
    np.testing.assert_array_equal(left.reset()[0], right.reset()[0])
    for i in range(69):
        action = (i // 6) % 3
        a, b = left.step(action), right.step(action)
        np.testing.assert_array_equal(a[0], b[0])
        assert a[1:] == b[1:]
        assert left.fills == right.fills
        assert left.cash == right.cash and left.quantity == right.quantity


def test_paired_prices_fee_step_and_balance_conservation():
    env = environment()
    result = finish(env)
    assert result['metrics']['fill_count'] > 4
    assert any(f['side'] == 'BUY' for f in result['fills'])
    assert any(f['side'] == 'SELL' for f in result['fills'])
    total_fees = Decimal(0)
    for fill in result['fills']:
        units = Decimal(fill['ounces'])
        assert units > 0 and units <= 10 and units % Decimal('.01') == 0
        assert fill['decision_at'] < fill['execution_at'] < env.end
        expected = Decimal(fill['ask'])+Decimal('.05') if fill['side'] == 'BUY' else Decimal(fill['bid'])-Decimal('.05')
        assert Decimal(fill['price']) == expected
        assert Decimal(fill['fee']) == units*Decimal('.10')
        assert Decimal(fill['cash_after']) >= 0 and Decimal(fill['quantity_after']) >= 0
        total_fees += Decimal(fill['fee'])
    assert total_fees == env.fees
    with localcontext() as precision:
        precision.prec = 60
        expected = env.realized+env.quantity*env._mark(env.index)-env.basis
        assert abs((env.value-env.costs.initial_cash)-expected) < Decimal('1e-30')
    assert result['trading_enabled'] is False


def test_first_fill_uses_next_boundary_and_requested_target_fraction():
    env = environment()
    env.step(2)
    fill = env.fills[0]
    assert fill['decision_at'] == START+100*HOUR
    assert fill['execution_at'] == START+100*HOUR+1000
    expected = (Decimal(5000)/(Decimal(fill['ask'])+Decimal('.15'))).quantize(Decimal('.01'), rounding=ROUND_FLOOR)
    assert Decimal(fill['ounces']) == expected


@pytest.mark.parametrize('late', [False, True])
def test_missing_or_late_open_expires_without_post_gap_fill(late):
    rows = synthetic_bars()
    opens = opening_quotes(rows)
    at = START+100*HOUR
    if late:
        _, bid, ask = opens[at]
        opens[at] = (at+120001, bid, ask)
    else:
        del opens[at]
    env = environment(rows, opens=opens)
    env.step(2)
    assert env.expired == 1 and env.fills == [] and env.quantity == 0
    env.step(0)
    assert env.fills == [] and env.quantity == 0


def test_independent_open_not_future_bar_coverage_controls_execution():
    rows = synthetic_bars()
    opens = opening_quotes(rows)
    # Those hours have opening quotes but lack complete subsequent bar coverage.
    incomplete_rows = rows[:100]+rows[102:]
    env = environment(incomplete_rows, opens=opens)
    env.step(2)
    assert env.fills[0]['execution_at'] == START+100*HOUR+1000
    assert env.index == 100 and env.bars[env.index].start == START+102*HOUR


def test_100_bar_warmup_window_end_and_episode_lifecycle():
    env = environment(synthetic_bars(102))
    assert env.index == 99
    with pytest.raises(RuntimeError, match='incomplete'):
        env.result()
    _, _, terminated, truncated, _ = env.step(2)
    assert not terminated and not truncated
    _, _, terminated, truncated, _ = env.step(0)
    assert not terminated and truncated
    before = list(env.fills)
    with pytest.raises(RuntimeError, match='reset'):
        env.step(2)
    assert before == env.fills
    assert all(f['execution_at'] < START+102*HOUR for f in before)
    with pytest.raises(ValueError, match='warmup'):
        environment(synthetic_bars(101))
    observation, _ = env.reset(seed=1)
    assert env.fills == [] and env.quantity == 0 and env.cash == 10000
    assert np.isfinite(observation).all()


def test_no_trade_cash_reference_retains_cash_and_has_zero_costs():
    env = environment()
    result = finish(env, lambda _: 0)
    assert Decimal(result['metrics']['total_return']) == 0
    assert result['metrics']['fill_count'] == 0
    assert env.cash == 10000 and env.quantity == 0 and env.fees == 0


@pytest.mark.parametrize('action', [-1, 3, float('nan'), float('inf'), 'BUY', [1], True])
def test_invalid_action_rejected_without_account_mutation(action):
    env = environment()
    original = env.index, env.cash, env.quantity, dict(env.actions)
    with pytest.raises(ValueError, match='action'):
        env.step(action)
    assert (env.index, env.cash, env.quantity, dict(env.actions)) == original
    assert env.fills == []


def test_submitted_allocation_cannot_be_bypassed_by_fixed_action_weights():
    # Regression: accepting .25 while action 2 uses .5 silently doubles the limit.
    with pytest.raises(ValueError, match='allocation'):
        environment(costs={**COSTS, 'allocation': '.25'})


def test_invalid_feature_matrix_quote_and_ordered_history_rejected():
    rows = synthetic_bars()
    features = market_features(rows)
    features[100, 0] = np.nan
    with pytest.raises(ValueError, match='feature'):
        environment(rows, features=features)
    with pytest.raises(ValueError, match='increasing'):
        environment(rows[:130]+[rows[129]]+rows[130:])
    opens = opening_quotes(rows)
    at = START+100*HOUR
    opens[at] = (at+1000, Decimal(2100), Decimal(2000))
    env = environment(rows, opens=opens)
    with pytest.raises(ValueError, match='paired'):
        env.step(2)
    assert env.fills == [] and env.quantity == 0 and env.cash == 10000


def test_gymnasium_environment_contract():
    check_env(environment(synthetic_bars(120)), skip_render_check=True)


def test_actual_ppo_training_save_reload_deterministic_actions(tmp_path):
    sb3 = pytest.importorskip('stable_baselines3')
    import torch
    torch.set_num_threads(1)
    env = environment(synthetic_bars(132), receipts=False)
    model = sb3.PPO('MlpPolicy', env, n_steps=32, batch_size=32, n_epochs=1,
                    policy_kwargs={'net_arch': [8, 8]}, seed=7, device='cpu', verbose=0)
    model.learn(total_timesteps=64)
    path = tmp_path/'own-trained-model.zip'
    model.save(path)
    reloaded = sb3.PPO.load(path, device='cpu')
    observation, _ = env.reset()
    for _ in range(5):
        first, _ = model.predict(observation, deterministic=True)
        second, _ = reloaded.predict(observation, deterministic=True)
        assert int(first) == int(second)
        observation = env.step(int(first))[0]


def test_exported_json_actor_matches_ppo_and_rejects_bad_contract():
    sb3 = pytest.importorskip('stable_baselines3')
    import torch
    from copy import deepcopy
    from scripts.study_gold_ppo import actor_action, export_actor
    torch.set_num_threads(1)
    env = environment(synthetic_bars(132))
    model = sb3.PPO('MlpPolicy', env, n_steps=32, batch_size=32, n_epochs=1,
                    policy_kwargs={'net_arch': [8, 8]}, seed=19, device='cpu', verbose=0)
    actor = export_actor(model)
    observation, _ = env.reset()
    for _ in range(8):
        action, _ = model.predict(observation, deterministic=True)
        assert actor_action(actor, observation) == int(action)
        observation = env.step(int(action))[0]
    mutations = []
    bad = deepcopy(actor); bad['version'] = 'unknown'; mutations.append(bad)
    bad = deepcopy(actor); bad['features'][0] = 'future_return'; mutations.append(bad)
    bad = deepcopy(actor); bad['actions'] = ['.5', '.25', '0']; mutations.append(bad)
    bad = deepcopy(actor); bad['layers'][0]['weight'][0][0] = float('nan'); mutations.append(bad)
    bad = deepcopy(actor); bad['layers'][0]['weight'][0].append(1); mutations.append(bad)
    bad = deepcopy(actor); bad['layers'][0]['kind'] = 'python'; mutations.append(bad)
    for invalid in mutations:
        with pytest.raises(ValueError):
            actor_action(invalid, observation)
    invalid_observation = observation.copy(); invalid_observation[0] = np.inf
    with pytest.raises(ValueError, match='observation'):
        actor_action(actor, invalid_observation)


def admission_rows():
    return [{'strategy': strategy, 'seed': seed, 'window': 'evaluation', 'scenario': scenario,
             'metrics': {'total_return': '.1', 'max_drawdown': '.01', 'fill_count': 3},
             'action_counts': [10, 20, 0]}
            for strategy, seeds in [('ppo_target_v1', [7, 19, 42]), ('buy_hold_reference', [None])]
            for seed in seeds for scenario in ['configured_costs', 'double_cost_stress']]


def test_admission_requires_every_predeclared_seed_scenario_once():
    from copy import deepcopy
    from scripts.study_gold_ppo import admission
    rows = admission_rows()
    assert admission(rows)['eligible'] is True
    assert admission(rows)['live_trading'] is False
    for replacement in [7, 999]:
        duplicated = deepcopy(rows)
        for row in duplicated:
            if row['strategy'] == 'ppo_target_v1':
                row['seed'] = replacement
        assert admission(duplicated)['eligible'] is False
    assert admission(rows[:-1])['eligible'] is False


@pytest.mark.parametrize('mutation', ['negative', 'constant', 'no_fills', 'drawdown'])
def test_admission_cannot_hide_a_failed_seed_in_average_results(mutation):
    from scripts.study_gold_ppo import admission
    rows = admission_rows()
    if mutation == 'negative':
        rows[0]['metrics']['total_return'] = '-.001'
    elif mutation == 'constant':
        rows[0]['action_counts'] = [0, 0, 100]
    elif mutation == 'no_fills':
        rows[0]['metrics']['fill_count'] = 0
    else:
        rows[0]['metrics']['max_drawdown'] = '.5'
    gate = admission(rows)
    assert gate['eligible'] is False and gate['reasons']


@pytest.mark.parametrize('field,value', [('total_return', 'NaN'), ('total_return', 'Infinity'),
                                        ('max_drawdown', '-.1'), ('max_drawdown', 'NaN'),
                                        ('fill_count', True), ('fill_count', -1)])
def test_admission_rejects_nonfinite_or_invalid_metrics(field, value):
    from scripts.study_gold_ppo import admission
    rows = admission_rows()
    rows[0]['metrics'][field] = value
    gate = admission(rows)
    assert gate['eligible'] is False and gate['reasons']


@pytest.mark.parametrize('counts', [[-1, 2, 3], [1, 2], [True, 2, 3], [1.5, 2, 3]])
def test_admission_rejects_invalid_action_counts(counts):
    from scripts.study_gold_ppo import admission
    rows = admission_rows()
    rows[0]['action_counts'] = counts
    gate = admission(rows)
    assert gate['eligible'] is False and gate['reasons']


def test_admission_rejects_nonfinite_passive_reference():
    from scripts.study_gold_ppo import admission
    rows = admission_rows()
    rows[-1]['metrics']['max_drawdown'] = 'NaN'
    gate = admission(rows)
    assert gate['eligible'] is False and gate['reasons']
