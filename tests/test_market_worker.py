import json
import time
import threading
from unittest.mock import patch
from apps.worker.market import consume_symbol,Publisher
from core.exchange.binance import RateLimited

class Cache:
    def __init__(self,stop=None):self.values={};self.emitted=[];self.stop=stop
    def set(self,key,value,ex):
        self.values[key]=value;self.emitted.append((key,json.loads(value),ex))
        if key.endswith(':book') and json.loads(value)['payload']['exchange_sequence']==201 and self.stop:self.stop.set()
    def delete(self,*keys):
        for key in keys:self.values.pop(key,None)

class Socket:
    def __init__(self,events):self.events=iter(events);self.closed=False
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    def close(self):self.closed=True
    def recv(self,timeout):
        if self.closed:raise ConnectionError('closed synthetic stream')
        try:return json.dumps({'data':next(self.events)})
        except StopIteration:time.sleep(.005);raise TimeoutError

class Adapter:
    def __init__(self):self.calls=0
    def depth(self,symbol):
        self.calls+=1
        return {'lastUpdateId':100 if self.calls==1 else 200,'bids':[['99','1']],'asks':[['101','1']]}

class Stop:
    def __init__(self):self.event=threading.Event();self.waits=[]
    def is_set(self):return self.event.is_set()
    def set(self):self.event.set()
    def wait(self,delay):self.waits.append(delay);return self.is_set()

def event(first,last):
    return {'e':'depthUpdate','s':'BTCUSDT','E':int(time.time()*1000),'U':first,'u':last,'b':[],'a':[]}

def test_stream_gap_reconnects_new_generation_no_stale_book():
    stop=Stop();cache=Cache(stop);adapter=Adapter()
    sockets=[Socket([event(101,101),event(103,103)]),Socket([event(201,201)])]
    with patch('apps.worker.market.connect',side_effect=sockets):consume_symbol('BTCUSDT',adapter,None,cache,stop)
    books=[v for k,v,_ in cache.emitted if k.endswith(':book')]
    assert [v['payload']['exchange_sequence'] for v in books]==[101,201]
    assert books[0]['generation']!=books[1]['generation']
    assert all(v['sequence']==1 for v in books)
    assert adapter.calls==2 and len(stop.waits)==1
    assert 'market:BTCUSDT:book' not in cache.values

def test_publisher_ttl_and_invalidation_clear_all_channels():
    cache=Cache();publisher=Publisher(cache,'BTCUSDT')
    publisher.emit('ticker',{'price':'100'})
    publisher.emit('ticker',{'price':'101'})
    assert cache.emitted[-1][1]['sequence']==2 and cache.emitted[-1][2]==15
    publisher.invalidate('synthetic_disconnect')
    assert 'market:BTCUSDT:ticker' not in cache.values
    assert json.loads(cache.values['market:BTCUSDT:status'])['state']=='unavailable'
