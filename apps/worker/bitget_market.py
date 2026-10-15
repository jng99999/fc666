"""Public REST snapshots into the existing expiring cache and WebSocket gateway."""
import logging
import time
from apps.worker.market import Publisher, backfill_symbol
from core.exchange.public import INTERVALS, RateLimited, utc_ms
from core.market_data.storage import save_candles, DataConflict, reconcile_confirmed_rest
from core.market_data import quality

log = logging.getLogger('fc666.bitget')

def consume_symbol(symbol, adapter, engine, cache, stop):
    publisher = Publisher(cache, symbol); attempt = 0
    while not stop.is_set():
        try:
            publisher.invalidate('connecting_bitget')
            backfill_symbol(symbol, adapter, engine)
            last_candles = 0
            persisted = {}
            while not stop.is_set():
                snapshots = adapter.snapshots(symbol)
                # Reuse the full existing freshness, decimal, ordering, and crossed-book validation.
                from apps.worker.market import envelope
                values = {channel: envelope(symbol, channel, publisher.generation, 1, payload, event)
                    for channel, payload, event in snapshots}
                reasons = quality.live_reasons(symbol, values['ticker'], values['book'],
                    utc_ms(int(time.time() * 1000)).isoformat())
                if reasons:
                    raise ValueError('Bitget live snapshot failed quality checks: ' + ','.join(reasons))
                for channel, payload, event in snapshots:
                    publisher.emit(channel, payload, event)
                if time.monotonic() - last_candles >= 8:
                    now = adapter.server_time()
                    for interval in INTERVALS:
                        bars = adapter.recent_candles(symbol, interval, now)
                        # Intrabar snapshots are for display. Read newly closed bars from
                        # the historical endpoint, with a short boundary publication grace.
                        step = INTERVALS[interval]
                        end = ((now - 3000) // step) * step
                        if persisted.get(interval) != end:
                            try:
                                closed = [bar for bar in adapter.candles(symbol, interval, end - 2 * step, now, now)
                                          if bar.close_time <= utc_ms(end)]
                                if not closed or closed[-1].close_time != utc_ms(end):
                                    raise DataConflict('Latest closed historical bar unavailable')
                                for bar in closed:
                                    try:
                                        save_candles(engine, [bar])
                                    except DataConflict:
                                        # Confirm each bar independently: a recent revision must
                                        # not roll back a settled older bar's audited correction.
                                        confirmation = [item for item in adapter.candles(symbol, interval,
                                            int(bar.open_time.timestamp() * 1000), now, now)
                                            if item.open_time == bar.open_time]
                                        reconcile_confirmed_rest(engine, [bar], confirmation, observed_at=utc_ms(now))
                                        save_candles(engine, [bar])
                                persisted[interval] = end
                            except DataConflict:
                                publisher.unavailable('candle.' + interval)
                                log.warning('bitget_closed_history_pending symbol=%s interval=%s', symbol, interval)
                                continue
                        if bars:
                            publisher.emit('candle.' + interval, bars[-1].model_dump(mode='json'), utc_ms(now).isoformat())
                    last_candles = time.monotonic()
                attempt = 0
                stop.wait(2)
        except Exception as exc:
            publisher.invalidate('bitget_unavailable')
            attempt += 1
            delay = exc.seconds if isinstance(exc, RateLimited) else min(30, 2 ** min(attempt, 5))
            log.warning('bitget_public_unavailable symbol=%s reason=%s retry=%s', symbol, type(exc).__name__, delay)
            stop.wait(delay)
    publisher.invalidate('worker_stopped')
