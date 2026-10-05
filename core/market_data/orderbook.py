"""Snapshot + absolute delta book, unusable until sequence continuity is verified."""
from decimal import Decimal, InvalidOperation
from core.exchange.binance import MarketDataError

class SequenceGap(MarketDataError):
    pass

class OrderBook:
    MAX_LEVELS=20000
    def __init__(self):
        self.sequence=None
        self.valid=False
        self.bids={}
        self.asks={}

    def invalidate(self):
        self.valid=False
        self.sequence=None
        self.bids.clear();self.asks.clear()

    @staticmethod
    def levels(rows):
        values=[]
        for price,quantity in rows:
            if not isinstance(price,str) or not isinstance(quantity,str): raise MarketDataError("Book requires decimal strings")
            try: p,q=Decimal(price),Decimal(quantity)
            except InvalidOperation as exc: raise MarketDataError("Malformed book level") from exc
            if not p.is_finite() or not q.is_finite() or p<=0 or q<0: raise MarketDataError("Invalid book level")
            values.append((p,q))
        return values

    def snapshot(self,data):
        self.invalidate()
        try:
            sequence=data["lastUpdateId"]
            if type(sequence) is not int or sequence<0: raise MarketDataError("Invalid snapshot sequence")
            self.bids={p:q for p,q in self.levels(data["bids"]) if q}
            self.asks={p:q for p,q in self.levels(data["asks"]) if q}
            self.sequence=sequence
            self.check_crossed()
        except Exception:
            self.invalidate();raise

    def apply(self,event):
        try:
            first,last=event["U"],event["u"]
            if type(first) is not int or type(last) is not int or first<0 or first>last:
                raise MarketDataError("Invalid delta sequence")
            if self.sequence is None: raise SequenceGap("Snapshot required")
            if last<=self.sequence: return False
            if first>self.sequence+1: raise SequenceGap("Missing book delta; resync required")
            bids,asks=self.levels(event["b"]),self.levels(event["a"])
            for target,changes in ((self.bids,bids),(self.asks,asks)):
                for p,q in changes:
                    if q==0: target.pop(p,None)
                    else: target[p]=q
            self.sequence=last
            self.check_crossed();self.valid=True
            return True
        except Exception:
            self.invalidate();raise

    def check_crossed(self):
        if len(self.bids)>self.MAX_LEVELS or len(self.asks)>self.MAX_LEVELS: raise MarketDataError("Orderbook memory bound exceeded; resync required")
        if not self.bids or not self.asks: raise MarketDataError("Incomplete orderbook")
        if self.bids and self.asks and max(self.bids)>=min(self.asks): raise MarketDataError("Crossed orderbook")

    def view(self,depth=20):
        if not self.valid: raise SequenceGap("Orderbook is invalid")
        return {"valid":True,"exchange_sequence":self.sequence,"snapshot_depth_limit":1000,
            "bids":[[str(p),str(q)] for p,q in sorted(self.bids.items(),reverse=True)[:depth]],
            "asks":[[str(p),str(q)] for p,q in sorted(self.asks.items())[:depth]]}
