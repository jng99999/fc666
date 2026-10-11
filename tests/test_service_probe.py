import json
import httpx
import pytest
from scripts.service_probe import capture,origin,MAX_BODY


def response(request):
    if request.url.path in ('/health/ready','/api/health'):
        return httpx.Response(200,json={'status':'ready','checks':{'database':True,'schema':True,'redis':True},'trading_enabled':False})
    return httpx.Response(200,json={'status':'healthy','symbols':{'BTCUSDT':'healthy','ETHUSDT':'healthy'},'trading_enabled':False})


def run(handler):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return capture('https://api.example.test','https://web.example.test',client=client)


def test_all_checks_and_scope():
    result=run(response)
    assert result['ready'] and len(result['checks'])==5
    assert result['trading_authority'] is False
    assert 'example.test' not in json.dumps(result)


@pytest.mark.parametrize('fault',['stale_symbol','redis_down','live_enabled','invalid','duplicate','large','redirect','timeout'])
def test_individual_failure_cannot_be_hidden(fault):
    def handler(request):
        if request.url.path!='/api/v1/market/status':return response(request)
        if fault=='stale_symbol':return httpx.Response(200,json={'status':'healthy','symbols':{'BTCUSDT':'healthy','ETHUSDT':'stale'},'trading_enabled':False})
        if fault=='redis_down':return httpx.Response(503,json={'status':'not_ready'})
        if fault=='live_enabled':return httpx.Response(200,json={'status':'healthy','symbols':{'BTCUSDT':'healthy','ETHUSDT':'healthy'},'trading_enabled':True})
        if fault=='invalid':return httpx.Response(200,text='private error content')
        if fault=='duplicate':return httpx.Response(200,text='{"trading_enabled":true,"trading_enabled":false,"status":"healthy"}')
        if fault=='large':return httpx.Response(200,content=b' '*(MAX_BODY+1))
        if fault=='redirect':return httpx.Response(302,headers={'location':'https://other.example.test'})
        raise httpx.ConnectError('secret transport content')
    result=run(handler)
    assert not result['ready']
    assert [check['service'] for check in result['checks'] if not check['ready']]==['market']
    assert 'private' not in json.dumps(result) and 'secret' not in json.dumps(result)


def test_ready_status_requires_dependency_booleans():
    def handler(request):
        if request.url.path=='/health/ready':return httpx.Response(200,json={'status':'ready','checks':{'database':True,'schema':True,'redis':False},'trading_enabled':False})
        return response(request)
    assert not run(handler)['ready']


@pytest.mark.parametrize('value',['http://api.example.test','https://user:secret@api.example.test','https://api.example.test?token=secret','https://api.example.test/path','https://api.example.test#secret','https://api.example.test:wrong'])
def test_reject_unsafe_origin(value):
    with pytest.raises(ValueError):origin(value)


def test_loopback_and_https_supported():
    assert origin('http://127.0.0.1:8000/')=='http://127.0.0.1:8000'
    assert origin('https://api.example.test')=='https://api.example.test'
