"""Causal input contract tests; fixtures are synthetic, not financial evidence."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

pytest.importorskip('gymnasium')
import numpy as np

from core.gold.history import HOUR
from core.gold.rl import FEATURES, market_features
from core.gold.rl_experiments import FrozenScaler, VERSION, actor_action, transform_market
from scripts.study_gold_ppo import export_actor
from tests.test_gold_rl import START, environment, synthetic_bars


def varied_bars():
    return [replace(b, start=b.start+(i//17)*HOUR,
                    first=b.first+(i//17)*HOUR, last=b.last+(i//17)*HOUR,
                    ask_close=b.ask_close+Decimal(i % 7)/100)
            for i, b in enumerate(synthetic_bars())]


def fit(rows):
    return FrozenScaler.fit(rows, START, rows[-1].start+HOUR)


def test_scaler_only_training_decisions_and_frozen_roundtrip():
    rows = varied_bars()
    scaler = fit(rows)
    x = market_features(rows)[99:-1].astype(np.float64)
    assert scaler.samples == len(rows)-100
    assert scaler.first_observed_at == rows[99].start+HOUR
    assert scaler.last_observed_at == rows[-2].start+HOUR
    np.testing.assert_array_equal(scaler.mean, x.mean(axis=0))
    np.testing.assert_array_equal(scaler.std, x.std(axis=0))
    assert FrozenScaler.from_payload(scaler.payload()) == scaler
    scaled = transform_market(x, 'B', scaler)
    np.testing.assert_allclose(scaled.mean(axis=0), 0, atol=1e-6)
    np.testing.assert_allclose(scaled.std(axis=0), 1, atol=1e-6)
    with pytest.raises(ValueError, match='prefix'):
        FrozenScaler.fit(rows, START, rows[-2].start+HOUR)


@pytest.mark.parametrize('arm', ['A', 'B', 'C'])
def test_future_suffix_cannot_change_scaled_prefix_or_account_execution(arm):
    rows = varied_bars()
    # Fit is frozen before either suffix is considered.
    scaler = None if arm == 'A' else fit(rows[:170])
    changed = rows[:170]+[replace(b, bid_close=b.bid_close*2, ask_close=b.ask_close*2,
                                  high=b.high*2, low=b.low*2) for b in rows[170:]]
    left_features = transform_market(market_features(rows), arm, scaler)
    right_features = transform_market(market_features(changed), arm, scaler)
    np.testing.assert_array_equal(left_features[:170], right_features[:170])
    left = environment(rows, features=left_features)
    right = environment(changed, features=right_features)
    for i in range(69):
        a, b = left.step(i % 3), right.step(i % 3)
        np.testing.assert_array_equal(a[0], b[0])
        assert a[1:] == b[1:] and left.fills == right.fills


def test_account_state_and_receipts_unchanged_by_feature_transform():
    rows = varied_bars()
    scaler, raw = fit(rows), market_features(rows)
    envs = [environment(rows, features=transform_market(raw, arm, None if arm == 'A' else scaler)) for arm in ('A', 'B', 'C')]
    for i in range(len(rows)-100):
        observations = [env.step(i % 3)[0] for env in envs]
        for observation in observations[1:]:
            np.testing.assert_array_equal(observation[7:], observations[0][7:])
        np.testing.assert_array_equal(observations[2][:7], np.zeros(7))
    assert envs[0].result() == envs[1].result() == envs[2].result()


def test_constant_nan_and_unknown_scale_contract_rejected():
    with pytest.raises(ValueError, match='scaler'):
        fit(synthetic_bars())  # elapsed_hours_log is constant; never invent unit std.
    scaler = fit(varied_bars())
    for values in ([float('nan')]*7, [float('inf')]*7):
        with pytest.raises(ValueError):
            replace(scaler, std=tuple(values))
    with pytest.raises(ValueError):
        replace(scaler, std=(0,)*7)
    raw = market_features(varied_bars())
    with pytest.raises(ValueError):
        transform_market(raw, 'B', None)
    with pytest.raises(ValueError):
        transform_market(raw, 'A', scaler)
    raw[100, 0] = np.nan
    with pytest.raises(ValueError):
        transform_market(raw, 'C', scaler)


@pytest.mark.parametrize('arm', ['A', 'B', 'C'])
def test_saved_json_actor_matches_reloaded_ppo_with_raw_observation(arm, tmp_path):
    import json
    import torch
    from stable_baselines3 import PPO
    torch.set_num_threads(1)
    rows = varied_bars()
    scaler = None if arm == 'A' else fit(rows)
    raw = market_features(rows)
    env = environment(rows, features=transform_market(raw, arm, scaler))
    model = PPO('MlpPolicy', env, n_steps=32, batch_size=32, n_epochs=1,
                policy_kwargs={'net_arch': [32, 32]}, seed=7, device='cpu', verbose=0)
    model.learn(total_timesteps=64)
    model.save(tmp_path/'own.zip')
    model = PPO.load(tmp_path/'own.zip', device='cpu')
    actor = {**export_actor(model), 'version': VERSION, 'input': 'raw_v1_observation',
             'arm': arm, 'scaler': None if scaler is None else scaler.payload()}
    actor = json.loads(json.dumps(actor))
    obs, _ = env.reset()
    for _ in range(80):
        expected, _ = model.predict(obs, deterministic=True)
        action = actor_action(actor, np.concatenate((raw[env.index], obs[7:])))
        assert action == int(expected)
        obs = env.step(action)[0]
    mutations = []
    bad = deepcopy(actor); bad['version'] = 'gold-ppo-target-v1'; mutations.append(bad)
    bad = deepcopy(actor); bad['features'] = list(reversed(FEATURES)); mutations.append(bad)
    bad = deepcopy(actor); bad['actions'] = ['.5', '.25', '0']; mutations.append(bad)
    bad = deepcopy(actor); bad['layers'][0]['weight'][0][0] = float('nan'); mutations.append(bad)
    bad = deepcopy(actor); bad['layers'][0]['weight'][0].append(1); mutations.append(bad)
    for invalid in mutations:
        with pytest.raises(ValueError):
            actor_action(invalid, np.concatenate((raw[env.index], obs[7:])))
