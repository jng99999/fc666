"""Immutable paired-tick history, separate from Swissquote live sampling."""
from dataclasses import dataclass
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

HOUR = 3600000
VERSION = 'histdata-paired-ticks-v2'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class Bar:
    start: int
    first: int
    last: int
    bid_open: Decimal
    ask_open: Decimal
    bid_close: Decimal
    ask_close: Decimal
    high: Decimal
    low: Decimal
    samples: int
    max_gap: int

    @property
    def close(self):
        return (self.bid_close + self.ask_close) / 2

    def row(self):
        return [self.start, self.first, self.last, *map(str, (self.bid_open, self.ask_open,
                self.bid_close, self.ask_close, self.high, self.low)), self.samples, self.max_gap]

    @classmethod
    def parse(cls, row, step=HOUR):
        if not isinstance(row, list) or len(row) != 11:
            raise ValueError('Invalid gold historical bar shape')
        if any(type(row[i]) is not int for i in (0, 1, 2, 9, 10)):
            raise ValueError('Gold historical times/counts must be integers')
        bar = cls(*row[:3], *[Decimal(v) for v in row[3:9]], *row[9:])
        prices = [bar.bid_open, bar.ask_open, bar.bid_close, bar.ask_close, bar.high, bar.low]
        if not all(p.is_finite() and p > 0 for p in prices):
            raise ValueError('Invalid historical gold price')
        if not (bar.bid_open <= bar.ask_open and bar.bid_close <= bar.ask_close and
                bar.low <= (bar.bid_open+bar.ask_open)/2 <= bar.high and bar.low <= bar.close <= bar.high):
            raise ValueError('Invalid historical bid/ask or OHLC')
        if not (bar.start % step == 0 and bar.start <= bar.first <= bar.last < bar.start+step and
                bar.first-bar.start <= 120000 and bar.start+step-bar.last <= 120000 and
                2 <= bar.samples and 0 <= bar.max_gap <= 300000):
            raise ValueError('Historical gold bar lacks complete observed coverage')
        return bar


def aggregate(bars, hours):
    if hours == 1:
        return list(bars)
    if hours != 4:
        raise ValueError('Gold research supports 1h and 4h')
    groups = {}
    for bar in bars:
        groups.setdefault(bar.start//(4*HOUR)*(4*HOUR), []).append(bar)
    result = []
    for start, group in sorted(groups.items()):
        if [b.start for b in group] != [start+i*HOUR for i in range(4)]:
            continue
        gap = max([b.max_gap for b in group]+[b.first-a.last for a, b in zip(group, group[1:])])
        if gap > 300000:
            continue
        first, last = group[0], group[-1]
        result.append(Bar(start, first.first, last.last, first.bid_open, first.ask_open,
                          last.bid_close, last.ask_close, max(b.high for b in group), min(b.low for b in group),
                          sum(b.samples for b in group), gap))
    return result


class Catalogue:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._cache = {}

    def load(self, identity):
        if not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Invalid gold dataset digest')
        if identity in self._cache:
            return self._cache[identity]
        path = self.directory / (identity+'.json.gz')
        if not path.exists():
            raise KeyError(identity)
        with gzip.open(path, 'rb') as handle:
            raw = handle.read(16000001)
        if len(raw) > 16000000:
            raise ValueError('Oversized gold historical snapshot')
        data = json.loads(raw)
        if sha(data) != identity or data.get('version') != VERSION or data.get('symbol') != 'XAU/USD':
            raise ValueError('Gold history digest/schema mismatch')
        if not 100 <= len(data['bars']) <= 50000:
            raise ValueError('Require 100..50000 observed gold hourly bars')
        bars = tuple(Bar.parse(row) for row in data['bars'])
        if any(b.start <= a.start for a, b in zip(bars, bars[1:])):
            raise ValueError('Gold history must be strictly increasing')
        if bars[0].start < data['start'] or bars[-1].start+HOUR > data['end']:
            raise ValueError('Historical gold date bounds mismatch')
        previous = -1
        for row in data['opens']:
            if (not isinstance(row, list) or len(row) != 4 or type(row[0]) is not int or
                    type(row[1]) is not int or row[0] % HOUR or not previous < row[0] or
                    not data['start'] <= row[0] < data['end'] or not row[0] <= row[1] < row[0]+HOUR):
                raise ValueError('Invalid independent gold opening quote sequence')
            bid, ask = Decimal(row[2]), Decimal(row[3])
            if not bid.is_finite() or not ask.is_finite() or not 0 < bid <= ask:
                raise ValueError('Invalid gold opening bid/ask')
            previous = row[0]
        data['identity'] = identity
        self._cache[identity] = (data, bars)
        return data, bars

    def list(self):
        rows = []
        for path in sorted(self.directory.glob('*.json.gz')):
            data, bars = self.load(path.name[:-8])
            rows.append({k: data[k] for k in ('identity', 'symbol', 'source', 'start', 'end', 'quality', 'version')})
            rows[-1]['bars'] = len(bars)
        return rows
