"""Two public Spot streams. UI cache snapshots are not a durable strategy bus."""
from collections import deque
from datetime import datetime,timezone
import json
import logging
from queue import Queue,Empty,Full
import random
import signal
import ssl
import threading
import time
from uuid import uuid4
from redis import Redis
from sqlalchemy import create_engine,select
from core.storage.models import CandleRecord
from websockets.sync.client import connect
from apps.api.settings import Settings
from core.exchange.binance import BinancePublic,SYMBOLS,INTERVALS,MarketDataError,RateLimited,normalize_candle,stream_event,utc_ms
from core.market_data.orderbook import OrderBook
from core.market_data.storage import save_instruments,save_candles,gap_report

log=logging.getLogger("fc666.market")
TTL=15
BACKFILL_BARS=120

def envelope(symbol,channel,generation,sequence,payload,event_time=None):
    return {"schema_version":1,"type":"snapshot","instrument_id":SYMBOLS[symbol],"symbol":symbol,
        "channel":channel,"generation":generation,"sequence":sequence,
        "event_time":event_time,"received_at":datetime.now(timezone.utc).isoformat(),
        "quality":"healthy","payload":payload}

class Publisher:
    def __init__(self,cache,symbol):
        self.cache,self.symbol=cache,symbol
        self.generation=str(uuid4())
        self.sequences={}

    def emit(self,channel,payload,event_time=None):
        seq=self.sequences.get(channel,0)+1;self.sequences[channel]=seq
        data=envelope(self.symbol,channel,self.generation,seq,payload,event_time)
        self.cache.set(f"market:{self.symbol}:{channel}",json.dumps(data),ex=TTL)

    def invalidate(self,reason):
        channels=["ticker","trade","book",*[f"candle.{i}" for i in INTERVALS]]
        self.cache.delete(*[f"market:{self.symbol}:{channel}" for channel in channels])
        self.cache.set(f"market:{self.symbol}:status",json.dumps({"state":"unavailable","reason":reason}),ex=TTL)


def backfill_symbol(symbol,adapter,engine):
    now=adapter.server_time()
    for interval,step in INTERVALS.items():
        end=(now//step)*step;start=end-BACKFILL_BARS*step
        with engine.connect() as conn:
            stored=list(conn.scalars(select(CandleRecord.open_time).where(
                CandleRecord.instrument_id==SYMBOLS[symbol],CandleRecord.timeframe==interval,
                CandleRecord.open_time>=utc_ms(start),CandleRecord.open_time<utc_ms(end))))
        expected=set(range(start,end,step))
        if {int(t.timestamp()*1000) for t in stored}==expected:continue
        candles=adapter.candles(symbol,interval,start,end,now)
        if gap_report(candles,interval,start,end)["missing_count"]:
            raise MarketDataError("Historical gap remains after backfill")
        save_candles(engine,candles)


def consume_symbol(symbol,adapter,engine,cache,stop):
    attempt=0
    while not stop.is_set():
        publisher=Publisher(cache,symbol);book=OrderBook()
        queue=Queue(maxsize=2048);session_stop=threading.Event()
        try:
            publisher.invalidate("connecting")
            if engine is not None: backfill_symbol(symbol,adapter,engine)
            names=[f"{symbol.lower()}@{name}" for name in ("ticker","trade","depth@100ms",*[f"kline_{i}" for i in INTERVALS])]
            uri="wss://data-stream.binance.vision/stream?streams="+"/".join(names)
            with connect(uri,ssl=ssl.create_default_context(),open_timeout=15,ping_interval=20,ping_timeout=20,
                max_size=1_000_000,max_queue=64,close_timeout=3,proxy=True) as socket:
                def reader():
                    try:
                        while not stop.is_set() and not session_stop.is_set():
                            try: raw=socket.recv(timeout=1)
                            except TimeoutError: continue
                            queue.put_nowait(json.loads(raw)["data"])
                    except Exception as exc:
                        try: queue.put_nowait(exc)
                        except Full: session_stop.set()
                        socket.close()
                thread=threading.Thread(target=reader,daemon=True);thread.start()
                try:
                    # Reader buffers deltas while REST snapshot is obtained.
                    book.snapshot(adapter.depth(symbol))
                    last_trade=-1;last_ticker=-1;last_candles={};recent=deque(maxlen=100)
                    last_depth=time.monotonic()
                    while not stop.is_set():
                        if time.monotonic()-last_depth>10: raise MarketDataError("Depth stream stale")
                        if session_stop.is_set(): raise MarketDataError("Stream queue overflow; resync required")
                        try: item=queue.get(timeout=1)
                        except Empty:
                            if time.monotonic()-last_depth>10: raise MarketDataError("Depth stream stale")
                            continue
                        if isinstance(item,Exception): raise item
                        if time.time()-item.get("E",0)/1000>15: raise MarketDataError("Delayed market data")
                        if item.get("s")!=symbol: raise MarketDataError("Unexpected stream symbol")
                        kind=item.get("e")
                        if kind=="depthUpdate":
                            if book.apply(item):
                                publisher.emit("book",book.view(),utc_ms(item["E"]).isoformat())
                                attempt=0;last_depth=time.monotonic()
                        elif kind in ("24hrTicker","trade"):
                            channel,payload=stream_event(symbol,item)
                            if channel=="trade":
                                if payload["trade_id"]<=last_trade: continue
                                last_trade=payload["trade_id"];recent.append(payload)
                                publisher.emit(channel,{"trades":list(recent)},payload["event_time"])
                            else:
                                if item["E"]<=last_ticker: continue
                                last_ticker=item["E"];publisher.emit(channel,payload,payload["event_time"])
                        elif kind=="kline":
                            k=item["k"];interval=k["i"]
                            if interval not in INTERVALS: raise MarketDataError("Unsupported stream interval")
                            if item["E"]<=last_candles.get(interval,-1): continue
                            last_candles[interval]=item["E"]
                            candle=normalize_candle(symbol,interval,[k["t"],k["o"],k["h"],k["l"],k["c"],k["v"],k["T"]],item["E"])
                            candle=candle.model_copy(update={"is_closed":bool(k["x"])})
                            if candle.is_closed: save_candles(engine,[candle])
                            publisher.emit(f"candle.{interval}",candle.model_dump(mode="json"),utc_ms(item["E"]).isoformat())
                        else: raise MarketDataError("Unknown stream event; reconnect")
                finally:
                    session_stop.set();socket.close();thread.join(timeout=2)
        except Exception as exc:
            book.invalidate()
            reason=type(exc).__name__
            try: publisher.invalidate(reason)
            except Exception: pass
            attempt+=1
            delay=exc.seconds if isinstance(exc,RateLimited) else min(30,2**min(attempt,5))+random.random()
            log.warning("market_stream_unavailable symbol=%s reason=%s retry_seconds=%.1f",symbol,reason,delay)
            stop.wait(delay)
    try: Publisher(cache,symbol).invalidate("worker_stopped")
    except Exception: pass


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings=Settings();cache=Redis.from_url(settings.redis_url,socket_timeout=3,socket_connect_timeout=3)
    engine=create_engine(settings.database_url.get_secret_value(),hide_parameters=True,pool_pre_ping=True,connect_args={"connect_timeout":2})
    adapter=BinancePublic();stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig,lambda *_:stop.set())
    try:
        # Rules must come from the exchange before any candle is persisted.
        while not stop.is_set():
            try:
                save_instruments(engine,adapter.instruments());break
            except Exception as exc:
                log.warning("market_initialization_unavailable reason=%s",type(exc).__name__)
                stop.wait(exc.seconds if isinstance(exc,RateLimited) else 15)
        if stop.is_set(): return
        threads=[threading.Thread(target=consume_symbol,args=(s,adapter,engine,cache,stop)) for s in SYMBOLS]
        for thread in threads: thread.start()
        while any(t.is_alive() for t in threads):
            for thread in threads: thread.join(timeout=1)
    finally:
        stop.set();adapter.close();engine.dispose();cache.close()

if __name__=="__main__": main()
