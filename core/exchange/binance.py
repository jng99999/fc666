"""Binance Spot public-only adapter. No credentials or write operations."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import ssl
import threading
import time
import httpx
from core.models import Candle, Instrument

SYMBOLS = {"BTCUSDT":"binance:spot:BTC-USDT", "ETHUSDT":"binance:spot:ETH-USDT"}
INTERVALS = {"1m":60_000, "5m":300_000, "15m":900_000, "1h":3_600_000, "4h":14_400_000, "1d":86_400_000}

class MarketDataError(Exception):
    pass
class RateLimited(MarketDataError):
    def __init__(self, seconds):
        self.seconds = seconds
        super().__init__("Public market data rate limited")

def utc_ms(value: int) -> datetime:
    if isinstance(value,bool) or not isinstance(value,int): raise MarketDataError("Invalid timestamp")
    return datetime.fromtimestamp(value / 1000, timezone.utc)

def symbol_id(symbol):
    if symbol not in SYMBOLS: raise ValueError("Unsupported public spot symbol")
    return SYMBOLS[symbol]

class BinancePublic:
    def __init__(self, client=None):
        self.owned = client is None
        self.client = client or httpx.Client(base_url="https://data-api.binance.vision", timeout=15,
            verify=ssl.create_default_context(), follow_redirects=False)
        self.lock = threading.Lock()
        self.next_request = 0.0

    def close(self):
        if self.owned: self.client.close()

    def get(self,path,params=None):
        # Conservative per-process request pacing; this isn't a distributed limiter.
        with self.lock:
            delay = self.next_request-time.monotonic()
            if delay>0: time.sleep(delay)
            self.next_request=time.monotonic()+1
        try: response = self.client.get(path,params=params)
        except httpx.HTTPError as exc: raise MarketDataError("Public REST transport unavailable") from exc
        if response.status_code in (418,429):
            try: delay=max(1,float(response.headers.get("Retry-After","60")))
            except ValueError: delay=60
            with self.lock: self.next_request=max(self.next_request,time.monotonic()+delay)
            raise RateLimited(delay)
        if response.status_code != 200: raise MarketDataError(f"Public REST rejected: HTTP {response.status_code}")
        try: return response.json()
        except ValueError as exc: raise MarketDataError("Invalid public REST JSON") from exc

    def server_time(self):
        value=self.get("/api/v3/time")["serverTime"]
        utc_ms(value)
        return value

    def instruments(self):
        result=[]
        response=self.get("/api/v3/exchangeInfo",{"symbols":json.dumps(list(SYMBOLS),separators=(",",":"))})
        for item in response["symbols"]:
            symbol=item["symbol"]
            if symbol not in SYMBOLS: continue
            if item["status"]!="TRADING" or not item.get("isSpotTradingAllowed",False):
                raise MarketDataError("Requested spot instrument is unavailable")
            filters={f["filterType"]:f for f in item["filters"]}
            notional=filters.get("NOTIONAL",filters.get("MIN_NOTIONAL"))
            if not notional: raise MarketDataError("Missing notional rule")
            result.append(Instrument(instrument_id=symbol_id(symbol),exchange="binance",native_symbol=symbol,
                base=item["baseAsset"],quote=item["quoteAsset"],market_type="SPOT",
                tick_size=filters["PRICE_FILTER"]["tickSize"],quantity_step=filters["LOT_SIZE"]["stepSize"],
                min_quantity=filters["LOT_SIZE"]["minQty"],min_notional=notional["minNotional"]))
        if {i.native_symbol for i in result}!=set(SYMBOLS): raise MarketDataError("Missing requested instruments")
        return result

    def candles(self,symbol,interval,start,end,server_time):
        symbol_id(symbol)
        if interval not in INTERVALS or start>=end: raise ValueError("Invalid historical range")
        cursor=start
        seen={}
        while cursor<end:
            rows=self.get("/api/v3/klines",{"symbol":symbol,"interval":interval,"startTime":cursor,"endTime":end-1,"limit":1000})
            if not rows: break
            last_open=cursor-1
            for row in rows:
                candle=normalize_candle(symbol,interval,row,server_time)
                if not start<=int(row[0])<end or int(row[0])<cursor:
                    raise MarketDataError("Kline outside requested page")
                key=candle.open_time
                if key in seen and seen[key]!=candle: raise MarketDataError("Conflicting duplicate candle")
                seen[key]=candle
                last_open=max(last_open,int(row[0]))
            if last_open<cursor: raise MarketDataError("Non-progressing candle pagination")
            cursor=last_open+INTERVALS[interval]
            if len(rows)<1000: break
        return sorted((c for c in seen.values() if c.is_closed),key=lambda c:c.open_time)

    def depth(self,symbol):
        symbol_id(symbol)
        return self.get("/api/v3/depth",{"symbol":symbol,"limit":1000})

def normalize_candle(symbol,interval,row,server_time):
    if interval not in INTERVALS: raise ValueError("Unsupported candle interval")
    if len(row)<7: raise MarketDataError("Incomplete kline")
    opened, closed = int(row[0]),int(row[6])+1
    if opened % INTERVALS[interval] or closed-opened != INTERVALS[interval]:
        raise MarketDataError("Misaligned kline timestamps")
    return Candle(instrument_id=symbol_id(symbol),timeframe=interval,open_time=utc_ms(opened),close_time=utc_ms(closed),
        open=row[1],high=row[2],low=row[3],close=row[4],volume=row[5],is_closed=closed<=server_time,source="binance.public")

def stream_event(symbol,message):
    """Normalize only the supported stream types, preserving decimal strings."""
    if message.get("s")!=symbol: raise MarketDataError("Unexpected stream symbol")
    kind=message.get("e")
    if kind=="24hrTicker":
        if Decimal(decimal_text(message["v"],positive=False))<0: raise MarketDataError("Negative ticker volume")
        return "ticker",{"price":decimal_text(message["c"]),"change_percent":decimal_text(message["P"],positive=False),
            "volume":decimal_text(message["v"],positive=False),"event_time":utc_ms(message["E"]).isoformat()}
    if kind=="trade":
        if type(message["t"]) is not int or message["t"]<0 or type(message["m"]) is not bool: raise MarketDataError("Invalid trade metadata")
        return "trade",{"trade_id":int(message["t"]),"price":decimal_text(message["p"]),
            "quantity":decimal_text(message["q"]),"aggressor_side":"SELL" if message["m"] else "BUY",
            "event_time":utc_ms(message["T"]).isoformat()}
    raise MarketDataError("Unsupported stream event")

def decimal_text(value,positive=True):
    if not isinstance(value,str): raise MarketDataError("Expected decimal string")
    try: number=Decimal(value)
    except Exception as exc: raise MarketDataError("Invalid decimal") from exc
    if not number.is_finite() or (positive and number<=0): raise MarketDataError("Invalid decimal range")
    return str(number)
