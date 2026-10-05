"""Synthetic protocol fixtures only; never represented as real market prices."""
from decimal import Decimal
import httpx
import pytest
from core.exchange.binance import BinancePublic,MarketDataError,RateLimited,normalize_candle,stream_event
from core.market_data.orderbook import OrderBook,SequenceGap
from core.market_data.storage import gap_report

@pytest.fixture
def book():
    result=OrderBook();result.snapshot({"lastUpdateId":100,"bids":[["99","2"]],"asks":[["101","3"]]})
    return result

def delta(first=101,last=101,bids=None,asks=None):
    return {"U":first,"u":last,"b":bids or [],"a":asks or []}

def test_snapshot_must_bridge_delta(book):
    assert not book.valid
    with pytest.raises(SequenceGap): book.view()
    assert book.apply(delta(100,101))
    assert book.view()["exchange_sequence"]==101

def test_absolute_quantity_delete_and_duplicates(book):
    book.apply(delta(bids=[["99","4"],["98","1"]],asks=[["101","0"],["102","2"]]))
    assert book.bids[Decimal("99")]==4
    assert Decimal("101") not in book.asks
    assert book.apply(delta()) is False
    assert book.view()["asks"]==[["102","2"]]

def test_sequence_gap_clears_book_and_recovery(book):
    book.apply(delta())
    with pytest.raises(SequenceGap): book.apply(delta(103,103))
    assert not book.valid and not book.bids and book.sequence is None
    with pytest.raises(SequenceGap): book.view()
    book.snapshot({"lastUpdateId":200,"bids":[["99","2"]],"asks":[["101","2"]]})
    book.apply(delta(201,201));assert book.valid

@pytest.mark.parametrize("event",[delta(bids=[["99","NaN"]]),delta(bids=[["99","-1"]]),delta(bids=[["102","1"]]),delta(102,101),delta(first=True)])
def test_bad_book_data_invalidates(book,event):
    with pytest.raises(MarketDataError): book.apply(event)
    assert not book.valid and not book.asks

def test_out_of_order_covered_delta_is_ignored(book):
    book.apply(delta(101,102))
    assert book.apply(delta(100,101,bids=[["99","100"]])) is False
    assert book.bids[Decimal("99")]==2

def row(opened=0):
    return [opened,"100","101","99","100.5","3",opened+59999]

def test_closed_candles_and_gap_reporting():
    first=normalize_candle("BTCUSDT","1m",row(),60000)
    unfinished=normalize_candle("BTCUSDT","1m",row(60000),119999)
    assert first.close_time.timestamp()*1000==60000 and first.is_closed
    assert not unfinished.is_closed
    report=gap_report([first],"1m",0,120000)
    assert report["missing_open_times_ms"]==[60000]

@pytest.mark.parametrize("change",[(1,59999),(0,60000)])
def test_wrong_interval_boundaries_rejected(change):
    sample=row();sample[0],sample[6]=change
    with pytest.raises(MarketDataError): normalize_candle("BTCUSDT","1m",sample,120000)

def test_adapter_paginates_without_future_or_duplicate_bars():
    calls=[]
    def handler(request):
        calls.append(int(request.url.params['startTime']))
        if len(calls)==1: return httpx.Response(200,json=[row(i*60000) for i in range(1000)])
        return httpx.Response(200,json=[row(60000000),row(60060000)])
    client=httpx.Client(transport=httpx.MockTransport(handler),base_url="https://test")
    adapter=BinancePublic(client)
    values=adapter.candles("BTCUSDT","1m",0,60120000,60060000)
    assert calls==[0,60000000]
    assert len(values)==1001 and all(c.is_closed for c in values)
    client.close()

def test_rate_limit_respects_retry_after():
    client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(429,headers={"Retry-After":"120"})),base_url="https://test")
    adapter=BinancePublic(client)
    with pytest.raises(RateLimited) as error:adapter.get("/api/v3/time")
    assert error.value.seconds==120
    client.close()

@pytest.mark.parametrize("status",[403,451,500])
def test_rejected_rest_never_returns_fake_data(status):
    client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(status)),base_url="https://test")
    with pytest.raises(MarketDataError):BinancePublic(client).get("/api/v3/time")
    client.close()

def test_real_rule_shape_normalization():
    items=[]
    for symbol,base in [("BTCUSDT","BTC"),("ETHUSDT","ETH")]:
        items.append({"symbol":symbol,"status":"TRADING","isSpotTradingAllowed":True,"baseAsset":base,"quoteAsset":"USDT","filters":[
            {"filterType":"PRICE_FILTER","tickSize":".01"},{"filterType":"LOT_SIZE","stepSize":".001","minQty":".001"},{"filterType":"NOTIONAL","minNotional":"5"}]})
    def handler(request):
        assert request.url.params["symbols"] == '["BTCUSDT","ETHUSDT"]'
        return httpx.Response(200,json={"symbols":items})
    client=httpx.Client(transport=httpx.MockTransport(handler),base_url="https://test")
    instruments=BinancePublic(client).instruments()
    assert instruments[0].instrument_id=="binance:spot:BTC-USDT"
    assert instruments[1].quantity_step==Decimal(".001")
    client.close()

def test_trade_event_aggressor_and_symbol_guard():
    event={"e":"trade","s":"BTCUSDT","t":42,"p":"100.01","q":".002","m":True,"T":60000}
    channel,payload=stream_event("BTCUSDT",event)
    assert channel=="trade" and payload["aggressor_side"]=="SELL"
    with pytest.raises(MarketDataError):stream_event("ETHUSDT",event)


def test_book_memory_bound_resyncs(book):
    book.MAX_LEVELS=2
    with pytest.raises(MarketDataError):book.apply(delta(bids=[["98","1"],["97","1"]]))
    assert not book.valid and not book.bids
