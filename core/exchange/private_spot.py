"""Fixed-host, read-only Binance Spot query boundary. Credentials never serialized."""
from dataclasses import dataclass,field
import hashlib,hmac,json,time,re
from urllib.parse import urlencode
import httpx
from pydantic import SecretStr
from core.market_data.quality import unique_object,number

BASE='https://api.binance.com'
MAX_BYTES=2*1024*1024


class PrivateQueryError(RuntimeError):pass


@dataclass(repr=False)
class Credentials:
    api_key:SecretStr=field(repr=False)
    api_secret:SecretStr=field(repr=False)

    def __repr__(self):return 'Credentials(<redacted>)'

    def __post_init__(self):
        if (not isinstance(self.api_key,SecretStr) or not isinstance(self.api_secret,SecretStr)
            or not 16<=len(self.api_key.get_secret_value())<=256 or not 16<=len(self.api_secret.get_secret_value())<=256
            or not re.fullmatch('[A-Za-z0-9]+',self.api_key.get_secret_value()) or not re.fullmatch('[A-Za-z0-9]+',self.api_secret.get_secret_value())):
            raise ValueError('Valid secret-backed private credentials required')


class ReadOnlySpot:
    def __init__(self,credentials,*,transport=None,clock_ms=None):
        if not isinstance(credentials,Credentials):raise ValueError('Private credentials required')
        self._credentials=credentials;self._transport=transport
        self._clock=clock_ms or (lambda:time.time_ns()//1_000_000)

    def _read(self,path,parameters):
        if path not in ['/api/v3/account','/api/v3/openOrders','/api/v3/myTrades']:raise ValueError('Unsupported read-only endpoint')
        stamp=self._clock()
        if type(stamp) is not int or not 0<stamp<2**63:raise ValueError('Bounded private request checkpoint required')
        params={**parameters,'timestamp':stamp,'recvWindow':5000}
        query=urlencode(sorted(params.items()))
        signature=hmac.new(self._credentials.api_secret.get_secret_value().encode(),query.encode(),hashlib.sha256).hexdigest()
        try:
            with httpx.Client(transport=self._transport,timeout=5,follow_redirects=False) as client:
                with client.stream('GET',BASE+path+'?'+query+'&signature='+signature,headers={'X-MBX-APIKEY':self._credentials.api_key.get_secret_value()}) as response:
                    if response.status_code!=200:raise PrivateQueryError('Private query rejected or unavailable')
                    raw=bytearray()
                    for chunk in response.iter_bytes():
                        if len(raw)+len(chunk)>MAX_BYTES:raise PrivateQueryError('Private response exceeds2MiB')
                        raw.extend(chunk)
            def reject(_):raise ValueError('Nonfinite private JSON')
            return json.loads(raw,object_pairs_hook=unique_object,parse_constant=reject)
        except Exception:
            # Do not propagate request URLs/signatures, raw remote messages or SDK exceptions.
            raise PrivateQueryError('Private read-only query failed') from None

    @staticmethod
    def _symbol(symbol):
        if symbol not in ['BTCUSDT','ETHUSDT']:raise ValueError('Supported private Spot symbol required')
        return symbol

    def account(self):
        result=self._read('/api/v3/account',{'omitZeroBalances':'false'})
        try:
            if not isinstance(result,dict) or result.get('accountType')!='SPOT' or not isinstance(result.get('balances'),list) or len(result['balances'])>2000:raise ValueError()
            balances=[];seen=set()
            for row in result['balances']:
                if not isinstance(row,dict) or set(row)!={'asset','free','locked'} or not isinstance(row['asset'],str) or not re.fullmatch('[A-Z0-9]{1,32}',row['asset']) or row['asset'] in seen:raise ValueError()
                seen.add(row['asset'])
                for key in ['free','locked']:
                    if number(row[key],positive=False)<0:raise ValueError()
                balances.append({key:row[key] for key in ['asset','free','locked']})
            return {'version':'private-spot-account-observation-v1','balances':sorted(balances,key=lambda b:b['asset']),
                    'read_only':True,'submission_allowed':False,'reconciled':False}
        except Exception:raise PrivateQueryError('Private account response cannot be verified') from None

    def open_orders(self,symbol):
        symbol=self._symbol(symbol);result=self._read('/api/v3/openOrders',{'symbol':symbol})
        return self._rows(result,symbol,'orders',1000,['orderId','clientOrderId','side','status','origQty','executedQty','price'])

    def trades(self,symbol,*,from_id=0):
        symbol=self._symbol(symbol)
        if type(from_id) is not int or not 0<=from_id<2**63:raise ValueError('Bounded trade cursor required')
        result=self._read('/api/v3/myTrades',{'symbol':symbol,'fromId':from_id,'limit':1000})
        normalized=self._rows(result,symbol,'trades',1000,['id','orderId','qty','price','commission','commissionAsset','time','isBuyer'])
        if any(row['id']<from_id for row in normalized['records']):raise PrivateQueryError('Private trade page precedes requested cursor')
        return normalized

    @staticmethod
    def _rows(result,symbol,kind,limit,fields):
        try:
            if not isinstance(result,list) or len(result)>limit:raise ValueError()
            rows=[];identities=set()
            for row in result:
                if not isinstance(row,dict) or row.get('symbol')!=symbol or any(key not in row for key in fields):raise ValueError()
                identity=row['orderId' if kind=='orders' else 'id']
                if type(identity) is not int or not 0<=identity<2**63 or identity in identities:raise ValueError()
                identities.add(identity)
                if kind=='orders':
                    if (row['side'] not in ['BUY','SELL'] or row['status'] not in ['NEW','PARTIALLY_FILLED','PENDING_CANCEL']
                        or not isinstance(row['clientOrderId'],str) or not 1<=len(row['clientOrderId'])<=128):raise ValueError()
                    for key in ['origQty','executedQty','price']:
                        if number(row[key],positive=False)<0:raise ValueError()
                    if number(row['origQty'])<number(row['executedQty'],positive=False):raise ValueError()
                else:
                    if (type(row['orderId']) is not int or not 0<=row['orderId']<2**63 or type(row['time']) is not int or not 0<=row['time']<2**63
                        or type(row['isBuyer']) is not bool or not isinstance(row['commissionAsset'],str) or not re.fullmatch('[A-Z0-9]{1,32}',row['commissionAsset'])):raise ValueError()
                    number(row['qty']);number(row['price'])
                    if number(row['commission'],positive=False)<0:raise ValueError()
                rows.append({key:row[key] for key in fields})
            return {'version':'private-spot-'+kind+'-observation-v1','symbol':symbol,'records':rows,
                    'coverage':'BOUNDED_RESPONSE_ONLY','pagination_complete':False,'read_only':True,'reconciled':False,'submission_allowed':False}
        except Exception:raise PrivateQueryError('Private '+kind+' response cannot be verified') from None
