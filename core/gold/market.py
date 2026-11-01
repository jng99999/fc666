"""Public XAU/USD bid/ask sampling; never substitutes tokens or another venue."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import time

import httpx

SOURCE = 'swissquote.public:AT:AT:standard:XAU/USD'
URL = 'https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD'


@dataclass(frozen=True)
class Quote:
    ts: int
    bid: Decimal
    ask: Decimal

    def validate(self, now):
        if not self.bid.is_finite() or not self.ask.is_finite() or not 0 < self.bid <= self.ask:
            raise ValueError('Invalid gold bid/ask')
        if self.ts > now + 5000 or now-self.ts > 60000:
            raise ValueError('Gold quote is stale or from the future')
        return self

    def json(self):
        return {'ts': self.ts, 'bid': str(self.bid), 'ask': str(self.ask),
                'mid': str((self.bid+self.ask)/2), 'spread': str(self.ask-self.bid),
                'source': SOURCE, 'symbol': 'XAU/USD', 'currency': 'USD', 'unit': 'troy_ounce'}


def parse(payload, now):
    if not isinstance(payload, list):
        raise ValueError('Gold quote envelope must be a list')
    venues = [row for row in payload if isinstance(row, dict) and row.get('topo') == {'platform': 'AT', 'server': 'AT'}]
    if len(venues) != 1:
        raise ValueError('Required gold quote venue unavailable')
    raw = venues[0].get('spreadProfilePrices')
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise ValueError('Invalid gold spread profile list')
    profiles = [row for row in raw if row.get('spreadProfile') == 'standard']
    if len(profiles) != 1:
        raise ValueError('Required gold spread profile unavailable')
    stamp = venues[0].get('ts')
    if isinstance(stamp, bool) or not isinstance(stamp, int):
        raise ValueError('Gold timestamp must be integer milliseconds')
    row = profiles[0]
    if isinstance(row.get('bid'), bool) or isinstance(row.get('ask'), bool):
        raise ValueError('Numeric gold prices required')
    try:
        return Quote(stamp, Decimal(str(row.get('bid'))), Decimal(str(row.get('ask')))).validate(now)
    except InvalidOperation as error:
        raise ValueError('Invalid numeric gold quote') from error


def fetch_quote():
    with httpx.Client(timeout=6, follow_redirects=False) as client:
        response = client.get(URL)
        response.raise_for_status()
        if len(response.content) > 128000:
            raise ValueError('Oversized gold quote response')
        return parse(response.json(), int(time.time()*1000))


def sampled_bars(quotes, now):
    """Observed 10-second samples only; no invented volume or missing minutes."""
    groups = {}
    for quote in quotes:
        minute = quote.ts//60000*60000
        if minute+60000 <= now:
            groups.setdefault(minute, []).append(quote)
    bars = []
    for minute, values in sorted(groups.items()):
        values.sort(key=lambda q: q.ts)
        # Require coverage at both ends and no material sampling outage.
        if values[0].ts-minute > 20000 or minute+60000-values[-1].ts > 20000:
            continue
        if any(b.ts-a.ts > 30000 for a, b in zip(values, values[1:])):
            continue
        mids = [(q.bid+q.ask)/2 for q in values]
        bars.append({'open_time': minute, 'close_time': minute+60000,
                     'open': str(mids[0]), 'high': str(max(mids)), 'low': str(min(mids)),
                     'close': str(mids[-1]), 'samples': len(values)})
    return bars


def analysis(bars):
    tail = []
    for bar in reversed(bars):
        if tail and bar['close_time'] != tail[-1]['open_time']:
            break
        tail.append(bar)
        if len(tail) == 20:
            break
    tail.reverse()
    if len(tail) < 20:
        return {'status': 'WARMUP', 'bars': len(tail), 'required': 20, 'sma20': None}
    sma = sum((Decimal(b['close']) for b in tail), Decimal(0))/20
    return {'status': 'READY', 'bars': 20, 'required': 20, 'sma20': str(sma),
            'target': 'LONG' if Decimal(tail[-1]['close']) > sma else 'FLAT',
            'available_at': tail[-1]['close_time'],
            'range_high': str(max(Decimal(b['high']) for b in tail)),
            'range_low': str(min(Decimal(b['low']) for b in tail))}
