"""Immutable quantity/cash admission policy for an exclusive local Paper pool."""
from copy import deepcopy
from decimal import localcontext
from sqlalchemy import select,text
from core.storage.models import PaperCapitalRiskPolicyRecord as Policy
from core.paper import requested_execution as execution,requested_inspection as inspection,requested_journal as journal
from core.paper.fault_adapter import sha,encoded

VERSION='paper-pool-risk-v1'
DECISION_VERSION='paper-pool-risk-admission-v1'
KEYS={'version','pool_id','max_quantity_by_instrument','max_active_requests','min_available_quote'}


def validate(value):
    if not isinstance(value,dict) or set(value)!=KEYS or value['version']!=VERSION:raise ValueError('Exact pool risk policy required')
    if type(value['pool_id']) is not str or not 1<=len(value['pool_id'])<=128:raise ValueError('Bounded pool identity required')
    caps=value['max_quantity_by_instrument']
    if not isinstance(caps,dict) or not 1<=len(caps)<=2 or any(key not in ['binance:spot:BTC-USDT','binance:spot:ETH-USDT'] for key in caps):raise ValueError('Supported instrument quantity caps required')
    for number in caps.values():execution.decimal(number,asset=True)
    execution.decimal(value['min_available_quote'])
    if type(value['max_active_requests']) is not int or not 1<=value['max_active_requests']<=100:raise ValueError('Bounded active request cap required')
    return deepcopy(value)


def check(db,pool_id):
    row=db.get(Policy,pool_id)
    if row is None:return None
    value=validate(row.payload)
    if value['pool_id']!=pool_id or row.payload_sha256!=sha(value):raise ValueError('Pool risk policy storage differs')
    return value


def enroll(engine,value,*,authorized_accounts=None):
    from core.paper import shared_capital_pool as pools
    value=validate(value)
    with journal.transaction(engine) as db:
        db.execute(text("SET LOCAL lock_timeout='2000ms'"))
        definition=pools.check(db,pools.lock(db,value['pool_id']));pools.authorize(definition,authorized_accounts)
        old=check(db,value['pool_id'])
        if old is not None:
            if encoded(old)!=encoded(value):raise ValueError('Conflicting immutable risk policy')
            return old
        reports=pools.journals(db,definition)
        if any(report['journal']['revision']!=0 for report in reports):raise ValueError('Risk policy cannot be retrofitted onto financial history')
        instruments={row['instrument_id'] for row in definition['allocations']}
        if set(value['max_quantity_by_instrument'])!=instruments:raise ValueError('Complete pool instrument caps required')
        db.add(Policy(pool_id=value['pool_id'],payload=value,payload_sha256=sha(value)));db.flush()
        return check(db,value['pool_id'])


def evaluate(value,pool_capture):
    from core.paper import shared_capital_pool as pools
    value=validate(value);capture=pools.verify(pool_capture);definition=capture['definition'];forecast=capture['preview']['forecast']
    if definition['pool_id']!=value['pool_id'] or forecast is None:raise ValueError('Pool candidate evidence required')
    instruments={row['instrument_id'] for row in definition['allocations']}
    if set(value['max_quantity_by_instrument'])!=instruments:raise ValueError('Incomplete pool risk coverage')
    quantities={key:execution.decimal('0') for key in instruments};active_count=0
    with localcontext() as context:
        context.prec=260
        for report in capture['preview']['journals']:
            fin=inspection.verify(report);instrument=fin['opening']['instrument_id']
            quantities[instrument]+=execution.decimal(fin['account']['quantity'],asset=True)
            entry=next((item for item in fin['requests'] if item['request']['request_id']==fin['active_request_id']),None)
            if entry is not None:
                active_count+=1
                if entry['request']['side']=='BUY':quantities[instrument]+=execution.decimal(entry['summary']['active_unfilled_quantity'],asset=True)
        request=forecast['request'];candidate_fin=next(report['journal'] for report in capture['preview']['journals'] if report['journal']['opening']['account_id']==request['account_id'])
        if request['side']=='BUY':quantities[candidate_fin['opening']['instrument_id']]+=execution.decimal(request['requested_quantity'],asset=True)
        projected_active=active_count+1
        available=execution.decimal(capture['preview']['available_quote_cash'])-execution.decimal(forecast['additional_reserved_quote'])
        reasons=[]
        if projected_active>value['max_active_requests']:reasons.append('ACTIVE_REQUEST_CAP')
        if available<execution.decimal(value['min_available_quote']):reasons.append('AVAILABLE_QUOTE_FLOOR')
        if any(q>execution.decimal(value['max_quantity_by_instrument'][key],asset=True) for key,q in quantities.items()):reasons.append('PROJECTED_QUANTITY_CAP')
        body={'version':DECISION_VERSION,'policy':value,'pool_capture':capture,'projected_quantity_by_instrument':{key:str(quantities[key]) for key in sorted(quantities)},
              'projected_active_requests':projected_active,'projected_available_quote':str(available),'reasons':reasons,
              'allowed':not reasons and forecast['capital_forecast_allowed'],'external_submission_allowed':False}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>execution.MAX_BYTES:raise ValueError('Risk decision exceeds32MiB')
    return result


def verify(value):
    if not isinstance(value,dict):raise ValueError('Risk decision required')
    expected=evaluate(value['policy'],value['pool_capture'])
    if encoded(expected)!=encoded(value):raise ValueError('Risk decision differs from replay')
    return expected
