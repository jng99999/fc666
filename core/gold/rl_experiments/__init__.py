"""Research-only v2 inputs and portable deterministic actor; no broker integration."""
from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np

from core.gold.history import HOUR
from core.gold.rl import FEATURES, market_features

Arm = Literal['A', 'B', 'C']
VERSION = 'gold-ppo-input-ablation-v2'
MARKET_FEATURES = FEATURES[:7]


@dataclass(frozen=True)
class FrozenScaler:
    mean: tuple[float, ...]
    std: tuple[float, ...]
    samples: int
    first_observed_at: int
    last_observed_at: int
    train_end: int

    def __post_init__(self):
        if (len(self.mean) != 7 or len(self.std) != 7
                or not np.isfinite(self.mean).all() or not np.isfinite(self.std).all()
                or min(self.std) <= 0 or self.samples < 2
                or not self.first_observed_at <= self.last_observed_at < self.train_end):
            raise ValueError('Invalid frozen training scaler')

    @classmethod
    def fit(cls, bars, train_start: int, train_end: int):
        """Only actual 2023 decision observations, excluding final truncation observation."""
        if (len(bars) < 102 or any(b.start < train_start or b.start+HOUR > train_end for b in bars)
                or any(b.start <= a.start for a, b in zip(bars, bars[1:]))):
            raise ValueError('Scaler fit requires strictly ordered training prefix only')
        x = market_features(bars)[99:-1].astype(np.float64)
        return cls(tuple(x.mean(axis=0)), tuple(x.std(axis=0)), len(x),
                   bars[99].start+HOUR, bars[-2].start+HOUR, train_end)

    def payload(self):
        return {**asdict(self), 'features': list(MARKET_FEATURES), 'ddof': 0,
                'clip': [-10, 10], 'fit': 'training_decision_observations_only'}

    @classmethod
    def from_payload(cls, data):
        if (data.get('features') != list(MARKET_FEATURES) or data.get('ddof') != 0
                or data.get('clip') != [-10, 10]
                or data.get('fit') != 'training_decision_observations_only'):
            raise ValueError('Scaler contract mismatch')
        return cls(tuple(data['mean']), tuple(data['std']), data['samples'],
                   data['first_observed_at'], data['last_observed_at'], data['train_end'])


def transform_market(values, arm: Arm, scaler: FrozenScaler | None):
    raw = np.asarray(values, dtype=np.float32)
    if raw.ndim not in (1, 2) or raw.shape[-1] != 7 or not np.isfinite(raw).all():
        raise ValueError('Invalid raw market features')
    if arm == 'A':
        if scaler is not None:
            raise ValueError('Arm A must retain v1 inputs')
        return raw.copy()
    if arm not in ('B', 'C') or scaler is None:
        raise ValueError('Arm B/C requires frozen scaler')
    scaled = np.clip((raw.astype(np.float64)-scaler.mean)/scaler.std, -10, 10).astype(np.float32)
    return np.zeros_like(scaled) if arm == 'C' else scaled


def actor_action(actor, observation):
    """Raw v1 ten-coordinate observation in; first argmax (0/1/2) out, float32 CPU."""
    if (actor.get('version') != VERSION or actor.get('features') != list(FEATURES)
            or actor.get('actions') != ['0', '.25', '.5']
            or actor.get('input') != 'raw_v1_observation'
            or actor.get('arm') not in ('A', 'B', 'C')):
        raise ValueError('V2 actor contract mismatch')
    values = np.asarray(observation, dtype=np.float32)
    if values.shape != (10,) or not np.isfinite(values).all():
        raise ValueError('Invalid v2 raw observation')
    scaler = None if actor['scaler'] is None else FrozenScaler.from_payload(actor['scaler'])
    values = np.concatenate((transform_market(values[:7], actor['arm'], scaler), values[7:]))
    values = np.clip(values, -10, 10).astype(np.float32)
    # Exact frozen 10 -> 32 -> 32 -> 3 actor architecture, no executable payloads.
    layers = actor['layers']
    if len(layers) != 5:
        raise ValueError('Invalid v2 actor architecture')
    for layer, shape in zip(layers, ((32, 10), None, (32, 32), None, (3, 32)), strict=True):
        if shape is None:
            if layer['kind'] != 'tanh':
                raise ValueError('Invalid v2 actor activation')
            values = np.tanh(values)
        else:
            if layer['kind'] != 'linear':
                raise ValueError('Invalid v2 actor layer')
            weight = np.asarray(layer['weight'], dtype=np.float32)
            bias = np.asarray(layer['bias'], dtype=np.float32)
            if (weight.shape != shape or bias.shape != (shape[0],)
                    or not np.isfinite(weight).all() or not np.isfinite(bias).all()):
                raise ValueError('Invalid v2 actor dimensions/weights')
            values = weight@values+bias
        if not np.isfinite(values).all():
            raise ValueError('Nonfinite v2 actor output')
    return int(np.argmax(values))
