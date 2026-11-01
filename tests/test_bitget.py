from decimal import Decimal
import httpx
import pytest
from core.exchange.bitget import BitgetPublic, normalize_candle, GRANULARITY
from core.exchange.binance import MarketDataError, RateLimited

def adapter(handler):
    return BitgetPublic(httpx.Client(base_url='https://api.bitget.com', transport=httpx.MockTransport(handler)))

def reply(data):
    return httpx.Response(200, json={'code': '00000', 'data': data})

def test_history_budget_reserves_separate_slots_without_delaying_live(monkeypatch):
    from core.exchange import bitget
    clock=[0.0];calls=[]
    monkeypatch.setattr(bitget.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(bitget.time,'sleep',lambda delay:clock.__setitem__(0,clock[0]+delay))
    feed=adapter(lambda request:(calls.append((request.url.path,clock[0])) or reply([])))
    feed.get('/api/v2/spot/market/history-candles')
    feed.get('/api/v2/spot/market/tickers')
    feed.get('/api/v2/spot/market/history-candles')
    assert calls[1][1]==pytest.approx(.1)
    assert calls[2][1]==pytest.approx(.5)

def test_provider_identity_and_utc_finality():
    assert GRANULARITY['1d'] == '1Dutc'
    bar = normalize_candle('BTCUSDT', '1m', ['60000', '100', '102', '99', '101', '1.23456789'], 120000)
    assert bar.instrument_id == 'bitget:spot:BTC-USDT' and bar.source == 'bitget.public'
    assert bar.volume == Decimal('1.23456789') and bar.is_closed
    assert not normalize_candle('BTCUSDT', '1m', ['60000', '100', '102', '99', '101', '1'], 119999).is_closed
    with pytest.raises(ValueError):
        normalize_candle('BTCUSDT', '1d', ['57600000', '100', '102', '99', '101', '1'], 200000000)

def test_history_pages_backwards_without_duplicate_or_future_rows():
    calls = []
    def handler(request):
        end = int(request.url.params['endTime']); calls.append(end)
        rows = [['120000', '100', '102', '99', '101', '1'], ['180000', '101', '102', '100', '101', '2']]
        if end <= 120000:
            rows = [['60000', '100', '102', '99', '101', '1']]
        return reply(rows)
    result = adapter(handler).candles('BTCUSDT', '1m', 60000, 240000, 240000)
    assert [int(bar.open_time.timestamp()*1000) for bar in result] == [60000,120000,180000]
    assert calls == [240000,120000]

def test_history_rejects_nonprogressing_page():
    api = adapter(lambda _:reply([['120000', '100', '102', '99', '101', '1']]))
    with pytest.raises(MarketDataError, match='progress'):
        api.candles('BTCUSDT','1m',0,120000,240000)

def test_upstream_errors_do_not_become_success():
    with pytest.raises(MarketDataError):
        adapter(lambda _:httpx.Response(200,json={'code':'400172','data':None})).server_time()
    with pytest.raises(RateLimited) as error:
        adapter(lambda _:httpx.Response(429,headers={'Retry-After':'2'})).server_time()
    assert error.value.seconds == 2

def test_full_book_and_large_trade_ids_remain_exact():
    def handler(request):
        if request.url.path.endswith('tickers'):
            return reply([{'symbol':'BTCUSDT','lastPr':'100','change24h':'0.0123','baseVolume':'3','ts':'120000'}])
        if request.url.path.endswith('orderbook'):
            return reply({'bids':[['99','2']],'asks':[['101','3']],'ts':'120000'})
        return reply([{'symbol':'BTCUSDT','tradeId':'1494549773819822080','price':'100','size':'1','side':'buy','ts':'120000'}])
    values = {channel:payload for channel,payload,_ in adapter(handler).snapshots('BTCUSDT')}
    assert values['ticker']['change_percent'] == '1.2300'
    assert values['book']['valid'] and values['book']['snapshot_depth_limit'] == 150
    assert values['trade']['trades'][0]['trade_id'] == '1494549773819822080'

def test_crossed_book_is_rejected():
    def handler(request):
        if request.url.path.endswith('tickers'):
            return reply([{'symbol':'BTCUSDT','lastPr':'100','change24h':'0','baseVolume':'3','ts':'120000'}])
        return reply({'bids':[['102','2']],'asks':[['101','3']],'ts':'120000'})
    with pytest.raises(MarketDataError,match='Crossed'):
        adapter(handler).snapshots('BTCUSDT')
