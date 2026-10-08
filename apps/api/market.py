import asyncio
from contextlib import suppress
from datetime import datetime,timezone
import json
from typing import Literal
from fastapi import APIRouter,HTTPException,Query,WebSocket,WebSocketDisconnect
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import select
from sqlalchemy.orm import Session
from core.models import Instrument,Candle
from core.exchange.binance import SYMBOLS,INTERVALS
from core.storage.models import InstrumentRecord,CandleRecord
from core.market_data import quality

Symbol=Literal["BTCUSDT","ETHUSDT"]
Interval=Literal["1m","5m","15m","1h","4h","1d"]
CHANNELS={"ticker","trade","book",*[f"candle.{i}" for i in INTERVALS]}

def market_health(cache):
    symbols={}
    for symbol in SYMBOLS:
        try:
            ticker,book=cache.mget([f"market:{symbol}:ticker",f"market:{symbol}:book"])
            reasons=quality.live_reasons(symbol,quality.decode(ticker),quality.decode(book),datetime.now(timezone.utc).isoformat())
            symbols[symbol]="healthy" if not reasons else "unavailable"
        except Exception: symbols[symbol]="unavailable"
    return {"status":"healthy" if all(v=="healthy" for v in symbols.values()) else "unavailable","symbols":symbols,"trading_enabled":False}

def router_for(engine,cache,redis_url):
    router=APIRouter()
    @router.get("/api/v1/instruments",response_model=list[Instrument])
    def instruments():
        with Session(engine) as session:
            return [Instrument.model_validate(i,from_attributes=True) for i in session.scalars(select(InstrumentRecord).order_by(InstrumentRecord.instrument_id))]

    @router.get("/api/v1/candles",response_model=list[Candle])
    def candles(symbol:Symbol,timeframe:Interval="1m",start:datetime|None=None,end:datetime|None=None,limit:int=Query(ge=1,le=1000,default=200)):
        for value in (start,end):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None): raise HTTPException(422,"UTC-aware range required")
        if start and end and start>=end: raise HTTPException(422,"start must precede end")
        query=select(CandleRecord).where(CandleRecord.instrument_id==SYMBOLS[symbol],CandleRecord.timeframe==timeframe)
        if start: query=query.where(CandleRecord.open_time>=start)
        if end: query=query.where(CandleRecord.open_time<end)
        with Session(engine) as session:
            items=list(session.scalars(query.order_by(CandleRecord.open_time.desc()).limit(limit)))
            return [Candle.model_validate(i,from_attributes=True) for i in reversed(items)]

    @router.get("/api/v1/indicators")
    def indicators(symbol:Symbol,timeframe:Interval="1m",as_of:datetime|None=None,limit:int=Query(ge=1,le=1000,default=200),period:int=Query(ge=1,le=500,default=20),oscillator_period:int=Query(ge=1,le=500,default=14),swing_radius:int=Query(ge=1,le=20,default=2)):
        from core.indicators.engine import calculate
        cutoff=as_of or datetime.now(timezone.utc)
        if cutoff.tzinfo is None or cutoff.utcoffset() is None: raise HTTPException(422,"UTC-aware as_of required")
        if cutoff>datetime.now(timezone.utc): raise HTTPException(422,"Future as_of is not available")
        query=select(CandleRecord).where(CandleRecord.instrument_id==SYMBOLS[symbol],CandleRecord.timeframe==timeframe,CandleRecord.is_closed.is_(True),CandleRecord.close_time<=cutoff).order_by(CandleRecord.open_time.desc()).limit(limit)
        with Session(engine) as session:
            bars=[Candle.model_validate(b,from_attributes=True) for b in reversed(list(session.scalars(query)))]
        try: rows=calculate(bars,as_of=cutoff,period=period,oscillator_period=oscillator_period,swing_radius=swing_radius)
        except ValueError as error: raise HTTPException(409,str(error))
        return {"schema_version":1,"symbol":symbol,"timeframe":timeframe,"as_of":cutoff,"period":period,"oscillator_period":oscillator_period,"swing_radius":swing_radius,"seed_start":bars[0].open_time if bars else None,"seed_policy":"SMA within requested window; no pre-window state","closed_only":True,"rows":rows}

    @router.get("/api/v1/market/status")
    def status(): return market_health(cache)

    @router.get("/api/v1/market/quality")
    def data_quality(symbol:Symbol):
        from fastapi.responses import JSONResponse
        return JSONResponse(content=quality.capture(engine,cache,symbol),headers={'Cache-Control':'no-store'})

    @router.get("/api/v1/market/snapshot")
    def snapshot(symbol:Symbol,channel:str="ticker"):
        if channel not in CHANNELS: raise HTTPException(422,"Unsupported channel")
        try: value=cache.get(f"market:{symbol}:{channel}")
        except Exception: raise HTTPException(503,"Market cache unavailable")
        if value is None: raise HTTPException(503,"Live data unavailable or stale")
        return json.loads(value)

    @router.websocket("/ws/v1/market")
    async def market_socket(socket:WebSocket):
        await socket.accept()
        receiver=None
        client=AsyncRedis.from_url(redis_url,socket_timeout=2,socket_connect_timeout=2)
        try:
            try: request=await asyncio.wait_for(socket.receive_json(),timeout=5)
            except (asyncio.TimeoutError,ValueError):
                await socket.close(code=1008,reason="Subscription required");return
            symbols=request.get("symbols",[]) if isinstance(request,dict) else []
            channels=request.get("channels",[]) if isinstance(request,dict) else []
            if not isinstance(request,dict) or request.get("action")!="subscribe" or not isinstance(symbols,list) or not isinstance(channels,list) or not 1<=len(symbols)<=2 or not 1<=len(channels)<=9 or any(s not in SYMBOLS for s in symbols if isinstance(s,str)) or any(not isinstance(s,str) for s in symbols) or any(not isinstance(c,str) or c not in CHANNELS for c in channels):
                await socket.close(code=1008,reason="Invalid subscription");return
            keys=[f"market:{s}:{c}" for s in dict.fromkeys(symbols) for c in dict.fromkeys(channels)]
            await socket.send_json({"type":"subscribed","symbols":symbols,"channels":channels,"delivery":"latest_snapshot","schema_version":1})
            previous={}
            receiver=asyncio.create_task(socket.receive_json())
            last_heartbeat=asyncio.get_running_loop().time()
            while True:
                if receiver.done():
                    command=receiver.result()
                    await socket.close(code=1000 if isinstance(command,dict) and command.get("action")=="unsubscribe" else 1008)
                    return
                if asyncio.get_running_loop().time()-last_heartbeat>5:
                    await asyncio.wait_for(socket.send_json({"type":"heartbeat"}),timeout=3)
                    last_heartbeat=asyncio.get_running_loop().time()
                try: values=await client.mget(keys)
                except Exception:
                    await socket.send_json({"type":"unavailable","reason":"cache_unavailable"});await socket.close(code=1013);return
                for key,value in zip(keys,values):
                    if previous.get(key,"initial")!=value:
                        if value is None:
                            await socket.send_json({"type":"unavailable","symbol":key.split(":")[1],"channel":key.split(":")[2],"reason":"missing_or_stale"})
                        else: await asyncio.wait_for(socket.send_json(json.loads(value)),timeout=3)
                        previous[key]=value
                await asyncio.sleep(.05)
        except (WebSocketDisconnect,asyncio.TimeoutError): pass
        finally:
            if receiver is not None:
                receiver.cancel()
                with suppress(asyncio.CancelledError,WebSocketDisconnect): await receiver
            await client.aclose()
    return router
