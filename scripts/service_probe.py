"""Bounded read-only readiness probe for schedulers and monitoring runners."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import json
import time
from urllib.parse import urlsplit

import httpx

MAX_BODY=16384


def origin(value):
    parsed=urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or not parsed.hostname:
        raise ValueError('Provide a credential-free service origin')
    try:
        loopback=ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback=parsed.hostname=='localhost'
    if parsed.scheme!='https' and not (parsed.scheme=='http' and loopback):
        raise ValueError('HTTPS required outside loopback')
    # Force validation of malformed ports before network work.
    parsed.port
    return value.rstrip('/')


def _pairs(items):
    result={}
    for key,value in items:
        if key in result:
            raise ValueError('Duplicate key')
        result[key]=value
    return result


def _constant(value):
    raise ValueError('Nonfinite JSON')


def _check(client,name,url):
    try:
        started=time.monotonic()
        with client.stream('GET',url,timeout=5,follow_redirects=False,headers={'Accept-Encoding':'identity'}) as response:
            if response.status_code!=200:
                return {'service':name,'ready':False,'reason':'http_unavailable'}
            body=bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic()-started>5:
                    return {'service':name,'ready':False,'reason':'response_deadline'}
                body.extend(chunk)
                if len(body)>MAX_BODY:
                    return {'service':name,'ready':False,'reason':'response_too_large'}
        value=json.loads(body,object_pairs_hook=_pairs,parse_constant=_constant)
        if not isinstance(value,dict) or value.get('trading_enabled') is not False:
            return {'service':name,'ready':False,'reason':'unsafe_or_invalid_response'}
        if name in ('api','web'):
            checks=value.get('checks')
            ready=value.get('status')=='ready' and isinstance(checks,dict) and all(checks.get(key) is True for key in ('database','schema','redis'))
        else:
            ready=value.get('status')=='healthy'
            if name=='market':
                symbols=value.get('symbols')
                ready=ready and isinstance(symbols,dict) and all(symbols.get(symbol)=='healthy' for symbol in ('BTCUSDT','ETHUSDT'))
        return {'service':name,'ready':bool(ready),'reason':'ready' if ready else 'not_ready'}
    except (httpx.HTTPError,ValueError,TypeError):
        # Never serialize exception text, remote bodies or configured URL credentials.
        return {'service':name,'ready':False,'reason':'transport_or_parse_failure'}


def capture(api_url,web_url,*,client=None):
    api_url,web_url=origin(api_url),origin(web_url)
    endpoints=[('api',api_url+'/health/ready'),('web',web_url+'/api/health'),('market',api_url+'/api/v1/market/status'),('research',api_url+'/api/v1/research/status'),('paper',api_url+'/api/v1/paper/status')]
    owned=client is None
    client=client or httpx.Client()
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            checks=list(executor.map(lambda entry:_check(client,*entry),endpoints))
        return {'version':'fc666-service-probe-v1','observed_at':datetime.now(timezone.utc).isoformat(),'ready':all(item['ready'] for item in checks),'checks':checks,'scope':'dependency_and_worker_readiness','trading_authority':False}
    finally:
        if owned:client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url',default='http://127.0.0.1:8000')
    parser.add_argument('--web-url',default='http://127.0.0.1:3000')
    parser.add_argument('--samples',type=int,default=1)
    parser.add_argument('--interval',type=float,default=10)
    args=parser.parse_args()
    if not 1<=args.samples<=120 or not 1<=args.interval<=60:
        parser.error('Samples must be 1..120 and interval 1..60 seconds')
    try:
        origin(args.api_url);origin(args.web_url)
    except ValueError:
        parser.error('Use credential-free HTTPS origins, or HTTP loopback origins')
    passed=True
    for index in range(args.samples):
        result=capture(args.api_url,args.web_url)
        print(json.dumps(result,separators=(',',':')),flush=True)
        passed=passed and result['ready']
        if index+1<args.samples:time.sleep(args.interval)
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
