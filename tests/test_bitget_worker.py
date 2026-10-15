from unittest.mock import Mock
from apps.worker import bitget_market as worker
from core.exchange.bitget import normalize_candle
from core.market_data.storage import DataConflict


def run_once(monkeypatch, conflict=False, now=123005):
    closed = normalize_candle('BTCUSDT', '1m', ['60000','100','102','99','101','1'], now)
    live = normalize_candle('BTCUSDT', '1m', ['120000','101','102','100','101','2'], now)
    adapter = Mock()
    adapter.snapshots.return_value = [('ticker', {}, ''), ('book', {}, ''), ('trade', {}, '')]
    adapter.server_time.return_value = now
    adapter.recent_candles.return_value = [closed, live]
    adapter.candles.return_value = [closed]
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
    publisher.emit.assert_any_call('candle.1m', live.model_dump(mode='json'), worker.utc_ms(123005).isoformat())


def test_recent_conflict_keeps_correction_gate_without_invalidating_live_quotes(monkeypatch):
    closed, _, save, reconcile, publisher = run_once(monkeypatch, conflict=True)
    save.assert_called_once_with(None, [closed])
    assert reconcile.call_args.args == (None, [closed], [closed])
    publisher.unavailable.assert_called_once_with('candle.1m')
    assert all(call.args[0]!='bitget_unavailable' for call in publisher.invalidate.call_args_list)
    publisher.emit.assert_any_call('ticker', {}, '')


def test_boundary_grace_withholds_candle_without_stopping_independent_quotes(monkeypatch):
    _, _, save, reconcile, publisher = run_once(monkeypatch, now=120005)
    save.assert_not_called()
    reconcile.assert_not_called()
    publisher.unavailable.assert_called_once_with('candle.1m')
    publisher.emit.assert_any_call('book', {}, '')
