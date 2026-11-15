"""Public closed candles and durable gold quote samples, never an order adapter."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock
import sqlite3
import time

import httpx

from core.exchange.bitget import BitgetPublic
from core.exchange.catalog import BITGET_SYMBOLS
from core.gold.market import Quote as GoldQuote, sampled_bars
from core.paper.directional import Quote, aware
from core.paper.directional_market import source_for, INSTRUMENTS
from core.paper.price_action_rules import RuleBar, INTERVALS
from core.backtest.spot import digest


def _empty_state():
    return {'bars': (), 'error': False, 'checked_at': None, 'error_reason': None,
            'revision_detected_at': None, 'resynchronized_at': None,
            'resume_after': None, 'old_sha256': None, 'new_sha256': None}


def _snapshot_sha(bars):
    return digest([bar.model_dump(mode='json') for bar in bars])


def bar_source(instrument):
    if instrument not in INSTRUMENTS:
        raise ValueError('Unsupported closed-bar product')
    return source_for(instrument) if instrument == 'XAU/USD' else 'bitget.public'


class RuleMarket:
    """History IO runs on its own collector, independent of protective quote ticks."""

    def __init__(self, path, *, settlement_seconds=0):
        if type(settlement_seconds) is not int or not 0 <= settlement_seconds <= 3600:
            raise ValueError('Settlement wait must be an integer from 0 to 3600 seconds')
        # A minimum arrival wait, never a guarantee that upstream cannot revise.
        # Gold uses its own observed sampling coverage and is not delayed.
        self.settlement_seconds = settlement_seconds
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.cache = {}
        self.candidates = {}
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

    def _available_boundary(self, instrument, timeframe, clock_ms):
        duration = INTERVALS[timeframe] * 1000
        wait = 0 if instrument == 'XAU/USD' else self.settlement_seconds * 1000
        return (clock_ms - wait) // duration * duration

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
        end = self._available_boundary(instrument, timeframe, min(server, local))
        candles = self.public.candles(instrument, timeframe, end - 500 * duration, end, min(server, local))
        if any(row.instrument_id != BITGET_SYMBOLS[instrument] or row.timeframe != timeframe
               or row.source != 'bitget.public' or row.is_closed is not True
               or int(row.close_time.timestamp() * 1000) > end for row in candles):
            raise ValueError('Public candle metadata does not match its requested scope')
        return [RuleBar(instrument=instrument, source=row.source, timeframe=timeframe,
                       open_time=row.open_time, close_time=row.close_time, open=row.open,
                       high=row.high, low=row.low, close=row.close, volume=row.volume, is_closed=row.is_closed) for row in candles]

    def refresh(self, instrument, timeframe, now, provider=None, *, recovery_after=None, on_state=None):
        key = (instrument, timeframe)
        now = aware(now)
        recovery_after = None if recovery_after is None else aware(recovery_after)
        if recovery_after is not None and recovery_after > now:
            raise ValueError('Recovery watermark cannot be in the future')
        with self.lock:
            previous = self.cache.get(key)
            forced = recovery_after is not None and (previous is None or previous['resynchronized_at'] is None
                                                       or previous['resynchronized_at'] <= recovery_after)
            if provider is None and not forced:
                if time.monotonic() - self.attempts.get(key, -100) < 5:
                    return
                if previous and not previous['error'] and previous['bars']:
                    boundary = self._available_boundary(instrument, timeframe, int(now.timestamp() * 1000))
                    if previous['bars'][-1].close_time.timestamp() * 1000 >= boundary:
                        return
            self.attempts[key] = time.monotonic()
        try:
            raw = (provider or self.fetch)(instrument, timeframe, now)
            if len(raw) > 500:
                raise ValueError('Closed history limit exceeded')
            bars = tuple(RuleBar.model_validate(row.model_dump() if isinstance(row, RuleBar) else row) for row in raw)
            boundary = self._available_boundary(instrument, timeframe, int(now.timestamp() * 1000))
            if any(b.instrument != instrument or b.source != bar_source(instrument) or b.timeframe != timeframe
                   or int(b.close_time.timestamp() * 1000) > boundary for b in bars):
                raise ValueError('Closed history product/source/clock mismatch')
            if any(a.open_time >= b.open_time or a.close_time > b.open_time for a, b in zip(bars, bars[1:])):
                raise ValueError('Closed history must be strictly ordered')
        except Exception:
            bars = None
        with self.lock:
            # A slower concurrent fetch cannot overwrite a newer published state.
            if self.cache.get(key) is not previous:
                return
            value = {**_empty_state(), **(previous or {}), 'checked_at': now}
            baseline = value['bars']
            candidate = self.candidates.get(key)
            changed = set()
            if bars is None or (not bars and baseline):
                value.update(error=True, error_reason='HISTORY_UNAVAILABLE')
                if candidate is not None:
                    # A failed/invalid intervening response breaks consecutive confirmation.
                    candidate = {**candidate, 'bars': None, 'sha256': None, 'checked_at': now}
            else:
                old = {row.open_time: row for row in baseline}
                fresh = {row.open_time: row for row in bars}
                changed = {stamp for stamp in old.keys() & fresh.keys() if old[stamp] != fresh[stamp]}
                sha = _snapshot_sha(bars)
                if changed or candidate is not None or forced:
                    revised_times = changed | (candidate['revised_times'] if candidate is not None else set())
                    original_returned = bool(candidate is not None and candidate['revised_times']
                        and candidate['revised_times'] <= fresh.keys() and not changed
                        and (recovery_after is None or now > recovery_after))
                    confirmed = bool(candidate is not None and candidate['bars'] is not None and bars
                        and candidate['sha256'] == sha and now > candidate['checked_at']
                        and (recovery_after is None or now > recovery_after))
                    if original_returned:
                        # Previously verified original data needs no new adopted baseline.
                        value.update(bars=bars, error=False, error_reason=None, resynchronized_at=now,
                                     resume_after=bars[-1].close_time)
                        candidate = None
                    elif confirmed:
                        value.update(bars=bars, error=False, error_reason=None, resynchronized_at=now,
                                     resume_after=bars[-1].close_time, new_sha256=sha)
                        candidate = None
                    else:
                        first_revision = bool(changed and candidate is None)
                        if first_revision:
                            value.update(revision_detected_at=now, old_sha256=_snapshot_sha(baseline))
                        elif forced and value['revision_detected_at'] is None:
                            value['revision_detected_at'] = now
                        value.update(error=True, error_reason='HISTORY_REVISED' if first_revision else 'HISTORY_RESYNC_PENDING')
                        if bars:
                            value['new_sha256'] = sha
                        candidate = {'bars': bars or None, 'sha256': sha if bars else None,
                                     'checked_at': now, 'revised_times': revised_times}
                else:
                    value.update(bars=bars, error=False, error_reason=None)
            self.cache[key] = value
            if candidate is None:
                self.candidates.pop(key, None)
            else:
                self.candidates[key] = candidate
            if on_state is not None:
                try:
                    # Keep publication and durable guard callback under the same
                    # reentrant lock held by the quote-admission guard.
                    on_state(value.copy())
                except BaseException:
                    # A failed durable write must never expose a tradable state.
                    self.cache[key] = {**value, 'bars': baseline, 'error': True,
                        'resynchronized_at': previous['resynchronized_at'] if previous else None,
                        'error_reason': value['error_reason'] or ('HISTORY_RESYNC_PENDING' if bars else 'HISTORY_UNAVAILABLE')}
                    if bars:
                        self.candidates[key] = {'bars': bars, 'sha256': _snapshot_sha(bars),
                            'checked_at': now, 'revised_times': changed}
                    raise

    def read(self, instrument, timeframe):
        with self.lock:
            return self.cache.get((instrument, timeframe), _empty_state()).copy()
