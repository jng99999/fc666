"""Public closed candles and durable gold quote samples, never an order adapter."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
import sqlite3
import time

import httpx

from core.exchange.bitget import BitgetPublic
from core.exchange.catalog import BITGET_SYMBOLS
from core.gold.market import Quote as GoldQuote, sampled_bars
from core.paper.directional import Quote, aware
from core.paper.directional_market import source_for, INSTRUMENTS
from core.paper.price_action_rules import RuleBar, INTERVALS


def bar_source(instrument):
    if instrument not in INSTRUMENTS:
        raise ValueError('Unsupported closed-bar product')
    return source_for(instrument) if instrument == 'XAU/USD' else 'bitget.public'


class RuleMarket:
    """History IO runs on its own collector, independent of protective quote ticks."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self.cache = {}
        self.attempts = {}
        self.client = httpx.Client(base_url='https://api.bitget.com', timeout=3, follow_redirects=False)
        self.public = BitgetPublic(self.client)
        with self.connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {'gold_samples'}:
                raise ValueError('Refusing an unrelated history database')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS gold_samples(at INTEGER PRIMARY KEY,bid TEXT NOT NULL,ask TEXT NOT NULL)')

    def connection(self):
        return sqlite3.connect(self.path, timeout=5)

    def close(self):
        self.client.close()

    def observe(self, value, now):
        quote = Quote.model_validate(value)
        if quote.instrument != 'XAU/USD':
            return
        now = aware(now)
        if quote.source != bar_source('XAU/USD') or not 0 <= (now - quote.at).total_seconds() <= 60:
            raise ValueError('Only fresh configured gold quotes can seed bars')
        stamp = int(quote.at.timestamp() * 1000)
        with self.connection() as db:
            existing = db.execute('SELECT bid,ask FROM gold_samples WHERE at=?', (stamp,)).fetchone()
            if existing and existing != (str(quote.bid), str(quote.ask)):
                raise ValueError('A finalized gold quote cannot be revised')
            db.execute('INSERT OR IGNORE INTO gold_samples VALUES(?,?,?)', (stamp, str(quote.bid), str(quote.ask)))
            # Only a bounded quote cache is trimmed; financial ledgers are unrelated.
            db.execute('DELETE FROM gold_samples WHERE at NOT IN (SELECT at FROM gold_samples ORDER BY at DESC LIMIT 3600)')

    def fetch(self, instrument, timeframe, now):
        now = aware(now)
        if timeframe not in INTERVALS or instrument not in INSTRUMENTS:
            raise ValueError('Unsupported closed-bar scope')
        if instrument == 'XAU/USD':
            if timeframe != '1m':
                raise ValueError('Gold supports actual one-minute quote samples only')
            with self.connection() as db:
                quotes = [GoldQuote(row[0], Decimal(row[1]), Decimal(row[2]))
                          for row in db.execute('SELECT at,bid,ask FROM gold_samples ORDER BY at')]
            return [RuleBar(instrument=instrument, source=bar_source(instrument), timeframe='1m',
                    open_time=datetime.fromtimestamp(row['open_time'] / 1000, timezone.utc),
                    close_time=datetime.fromtimestamp(row['close_time'] / 1000, timezone.utc),
                    open=row['open'], high=row['high'], low=row['low'], close=row['close'], volume=None, is_closed=True)
                    for row in sampled_bars(quotes, int(now.timestamp() * 1000))[-500:]]
        server = self.public.server_time()
        local = int(now.timestamp() * 1000)
        if abs(server - local) > 60000:
            raise ValueError('Public candle clock differs from the server clock')
        duration = INTERVALS[timeframe] * 1000
        end = min(server, local) // duration * duration
        candles = self.public.candles(instrument, timeframe, end - 500 * duration, end, min(server, local))
        if any(row.instrument_id != BITGET_SYMBOLS[instrument] or row.timeframe != timeframe
               or row.source != 'bitget.public' or row.is_closed is not True for row in candles):
            raise ValueError('Public candle metadata does not match its requested scope')
        return [RuleBar(instrument=instrument, source=row.source, timeframe=timeframe,
                       open_time=row.open_time, close_time=row.close_time, open=row.open,
                       high=row.high, low=row.low, close=row.close, volume=row.volume, is_closed=row.is_closed) for row in candles]

    def refresh(self, instrument, timeframe, now, provider=None):
        key = (instrument, timeframe)
        now = aware(now)
        with self.lock:
            previous = self.cache.get(key)
            if provider is None:
                if time.monotonic() - self.attempts.get(key, -100) < 5:
                    return
                if previous and not previous['error'] and previous['bars']:
                    boundary = int(now.timestamp()) // INTERVALS[timeframe] * INTERVALS[timeframe]
                    if previous['bars'][-1].close_time.timestamp() >= boundary:
                        return
            self.attempts[key] = time.monotonic()
        try:
            raw = (provider or self.fetch)(instrument, timeframe, now)
            if len(raw) > 500:
                raise ValueError('Closed history limit exceeded')
            bars = tuple(RuleBar.model_validate(row.model_dump() if isinstance(row, RuleBar) else row) for row in raw)
            if any(b.instrument != instrument or b.source != bar_source(instrument) or b.timeframe != timeframe
                   or b.close_time > now for b in bars):
                raise ValueError('Closed history product/source/clock mismatch')
            if any(a.open_time >= b.open_time or a.close_time > b.open_time for a, b in zip(bars, bars[1:])):
                raise ValueError('Closed history must be strictly ordered')
            if previous and previous['bars']:
                old = {b.open_time: b for b in previous['bars']}
                if any(b.open_time in old and old[b.open_time] != b for b in bars):
                    raise ValueError('A previously observed closed candle was revised')
            value = {'bars': bars, 'error': False, 'checked_at': now}
        except Exception:
            value = {'bars': previous['bars'] if previous else (), 'error': True, 'checked_at': now}
        with self.lock:
            self.cache[key] = value

    def read(self, instrument, timeframe):
        with self.lock:
            return self.cache.get((instrument, timeframe), {'bars': (), 'error': False, 'checked_at': None}).copy()
