from unittest.mock import Mock
from apps.worker import bitget_market as worker
from core.exchange.bitget import normalize_candle
from core.market_data.storage import DataConflict


def run_once(monkeypatch, conflict=False):
    now = 120005
    closed = normalize_candle('BTCUSDT', '1m', ['60000','100','102','99','101','1'], now)
    live = normalize_candle('BTCUSDT', '1m', ['120000','101','102','100','101','2'], now)
    adapter = Mock()
    adapter.snapshots.return_value = [('ticker', {}, ''), ('book', {}, ''), ('trade', {}, '')]
    adapter.server_time.return_value = now
    adapter.recent_candles.return_value = [closed, live]
    stop = Mock()
    stop.is_set.side_effect = [False, False, True, True]
    save = Mock(side_effect=DataConflict('stored bar differs') if conflict else None)
    reconcile = Mock(side_effect=DataConflict('REST correction too recent') if conflict else None)
    publisher = Mock()
    monkeypatch.setattr(worker, 'Publisher', Mock(return_value=publisher))
    monkeypatch.setattr(worker, 'backfill_symbol', Mock())
    monkeypatch.setattr(worker, 'INTERVALS', {'1m': 60000})
    monkeypatch.setattr(worker.quality, 'live_reasons', Mock(return_value=[]))
    monkeypatch.setattr(worker, 'save_candles', save)
    monkeypatch.setattr(worker, 'reconcile_confirmed_rest', reconcile)
    worker.consume_symbol('BTCUSDT', adapter, None, None, stop)
    return closed, live, save, reconcile, publisher


def test_new_closed_bar_is_persisted_without_one_minute_delay(monkeypatch):
    closed, live, save, reconcile, publisher = run_once(monkeypatch)
    save.assert_called_once_with(None, [closed])
    reconcile.assert_not_called()
    publisher.emit.assert_any_call('candle.1m', live.model_dump(mode='json'), worker.utc_ms(120005).isoformat())


def test_recent_conflict_keeps_correction_gate_and_invalidates_feed(monkeypatch):
    closed, _, save, reconcile, publisher = run_once(monkeypatch, conflict=True)
    save.assert_called_once_with(None, [closed])
    assert reconcile.call_args.args == (None, [closed], [closed])
    publisher.invalidate.assert_any_call('bitget_unavailable')
