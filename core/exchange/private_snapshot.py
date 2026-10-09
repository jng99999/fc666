"""Bounded read-only snapshots; sequential queries do not prove reconciliation."""
from copy import deepcopy
import json,os,stat,re
from datetime import datetime,timezone
from pydantic import SecretStr
from core.exchange.private_spot import Credentials,ReadOnlySpot,PrivateQueryError
from core.market_data.quality import unique_object,number
from core.paper.fault_adapter import encoded,sha

VERSION='private-spot-snapshot-v1'
MAX_BYTES=8*1024*1024


def credentials_file(path):
    fd=None
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>2048:raise ValueError()
        with os.fdopen(fd,'rb') as stream:
            fd=None;raw=stream.read(2049)
        if len(raw)>2048:raise ValueError()
        value=json.loads(raw,object_pairs_hook=unique_object)
        if not isinstance(value,dict) or set(value)!={'api_key','api_secret'}:raise ValueError()
        return Credentials(SecretStr(value['api_key']),SecretStr(value['api_secret']))
    except Exception:raise PrivateQueryError('Invalid owner-only credential file') from None
    finally:
        if fd is not None:os.close(fd)


def verify(value):
    keys={'version','scope','symbol','from_id','started_at','completed_at','account','orders','trades','atomic_snapshot','identity_verified','reconciled','submission_allowed','sha256'}
    if not isinstance(value,dict) or set(value)!=keys or value['version']!=VERSION or len(encoded(value).encode())>MAX_BYTES:raise ValueError('Invalid bounded private snapshot')
    if type(value['scope']) is not str or not re.fullmatch('[A-Za-z0-9_.-]{1,64}',value['scope']):raise ValueError('Bounded local credential label required')
    ReadOnlySpot._symbol(value['symbol'])
    if type(value['from_id']) is not int or not 0<=value['from_id']<2**63:raise ValueError('Invalid cursor')
    from core.paper.requested_execution import clock
    if clock(value['completed_at'])<clock(value['started_at']):raise ValueError('Snapshot clock regression')
    account=value['account']
    if not isinstance(account,dict) or set(account)!={'version','balances','read_only','submission_allowed','reconciled'} or account['version']!='private-spot-account-observation-v1' or not isinstance(account['balances'],list) or len(account['balances'])>2000:raise ValueError('Invalid account observation')
    seen=set()
    for row in account['balances']:
        if not isinstance(row,dict) or set(row)!={'asset','free','locked'} or type(row['asset']) is not str or not re.fullmatch('[A-Z0-9]{1,32}',row['asset']) or row['asset'] in seen:raise ValueError('Invalid balance identity')
        seen.add(row['asset'])
        if any(number(row[k],positive=False)<0 for k in ['free','locked']):raise ValueError('Negative balance')
    if account['balances']!=sorted(account['balances'],key=lambda r:r['asset']) or account['read_only'] is not True or account['submission_allowed'] is not False or account['reconciled'] is not False:raise ValueError('Account flags/order differ')
    for kind,fields in [('orders',['orderId','clientOrderId','side','status','origQty','executedQty','price']),('trades',['id','orderId','qty','price','commission','commissionAsset','time','isBuyer'])]:
        observed=value[kind]
        if not isinstance(observed,dict) or not isinstance(observed.get('records'),list) or len(observed['records'])>1000 or any(not isinstance(row,dict) or set(row)!=set(fields) for row in observed['records']):raise ValueError('Invalid normalized rows')
        expected=ReadOnlySpot._rows([{**row,'symbol':value['symbol']} for row in observed['records']],value['symbol'],kind,1000,fields)
        if expected!=observed:raise ValueError('Private row evidence differs')
    if any(row['id']<value['from_id'] for row in value['trades']['records']):raise ValueError('Trade cursor differs')
    if any(value[k] is not False for k in ['atomic_snapshot','identity_verified','reconciled','submission_allowed']) or value['sha256']!=sha({k:v for k,v in value.items() if k!='sha256'}):raise ValueError('Private snapshot flags/hash differ')
    return deepcopy(value)


def capture(client,scope,symbol,from_id=0):
    if not isinstance(client,ReadOnlySpot) or type(scope) is not str or not re.fullmatch('[A-Za-z0-9_.-]{1,64}',scope):raise ValueError('Read-only client and local scope required')
    ReadOnlySpot._symbol(symbol)
    if type(from_id) is not int or not 0<=from_id<2**63:raise ValueError('Invalid cursor')
    start=datetime.now(timezone.utc).isoformat()
    account=client.account();orders=client.open_orders(symbol);trades=client.trades(symbol,from_id=from_id)
    body=dict(version=VERSION,scope=scope,symbol=symbol,from_id=from_id,started_at=start,completed_at=datetime.now(timezone.utc).isoformat(),account=account,orders=orders,trades=trades,atomic_snapshot=False,identity_verified=False,reconciled=False,submission_allowed=False)
    return verify({**body,'sha256':sha(body)})


def compare(before,after):
    before=verify(before);after=verify(after)
    if any(before[k]!=after[k] for k in ['scope','symbol','from_id']):raise ValueError('Comparison scope/cursor differs')
    from core.paper.requested_execution import clock
    if clock(after['started_at'])<clock(before['completed_at']):raise ValueError('Comparison snapshots overlap or regress')
    differences=[]
    for section,field,identity in [('account','balances','asset'),('orders','records','orderId'),('trades','records','id')]:
        old={r[identity]:r for r in before[section][field]};new={r[identity]:r for r in after[section][field]}
        for key in sorted(set(old)|set(new)):
            if old.get(key)!=new.get(key):differences.append(dict(section=section,identity=key,before=old.get(key),after=new.get(key)))
    body=dict(version='private-spot-differences-v1',before=before,after=after,differences=differences,verdict='DIFFERENCES_FOUND' if differences else 'NO_DIFFERENCES_IN_OBSERVED_SCOPE',coverage='NONATOMIC_BALANCES_OPEN_ORDERS_BOUNDED_TRADES',identity_verified=False,reconciled=False,automatic_repair=False,submission_allowed=False)
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>3*MAX_BYTES:raise ValueError('Comparison exceeds bounds')
    return result


def read(path):
    with open(path,'rb') as stream:raw=stream.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Snapshot exceeds bounds')
    def reject(_):raise ValueError('Nonfinite JSON')
    return json.loads(raw,object_pairs_hook=unique_object,parse_constant=reject)


def write(path,value):
    raw=encoded(value).encode()
    if len(raw)>3*MAX_BYTES:raise ValueError('Artifact exceeds bounds')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
