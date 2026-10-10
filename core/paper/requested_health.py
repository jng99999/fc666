"""Opt-in local health evidence; capture at new writes, replay at historical reads."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import re
from sqlalchemy import select,text
from core.market_data import quality
from core.paper import requested_execution as contract,requested_journal as journal,requested_controls as controls,requested_inspection as inspection
from core.paper.fault_adapter import encoded,sha
from core.storage.models import RequestedPaperHealthPolicyRecord as Policy,RequestedPaperHealthGateRecord as Gate

POLICY_VERSION='paper-requested-health-policy-v1'
GATE_VERSION='paper-requested-health-gate-v1'
AGE_US=2_000_000
MAX_STORED_BYTES=32*1024*1024


def policy(opening,control_revision):
    symbol=next((symbol for symbol,identity in quality.SYMBOLS.items() if identity==opening['instrument_id']),None)
    if symbol is None:raise ValueError('Health enrollment requires a supported public Spot instrument')
    value={'version':POLICY_VERSION,'account_id':opening['account_id'],'instrument_id':opening['instrument_id'],
           'symbol':symbol,'opening_sha256':sha(opening),'enrolled_control_revision':control_revision,
           'max_observation_age_us':AGE_US,'phases':['PREPARE','SUBMIT']}
    return validate_policy(value)


def validate_policy(value):
    keys={'version','account_id','instrument_id','symbol','opening_sha256','enrolled_control_revision','max_observation_age_us','phases'}
    if (not isinstance(value,dict) or set(value)!=keys or value['version']!=POLICY_VERSION
        or not isinstance(value['account_id'],str) or not 1<=len(value['account_id'])<=128
        or not isinstance(value['symbol'],str) or quality.SYMBOLS.get(value['symbol'])!=value['instrument_id']
        or not isinstance(value['opening_sha256'],str) or re.fullmatch('[0-9a-f]{64}',value['opening_sha256']) is None
        or type(value['enrolled_control_revision']) is not int or not 1<=value['enrolled_control_revision']<=controls.CONTROL_LIMIT
        or type(value['max_observation_age_us']) is not int or value['max_observation_age_us']!=AGE_US
        or value['phases']!=['PREPARE','SUBMIT']):raise ValueError('Invalid immutable health policy')
    return deepcopy(value)


def fresh(report,checked_us):
    if type(checked_us) is not int or not 0<=checked_us<=253402300799000000:
        raise ValueError('Bounded database health checkpoint required')
    now=datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(microseconds=checked_us)
    age=now-quality.clock(report['evidence']['observed_at'])
    if not -timedelta(microseconds=AGE_US)<=age<=timedelta(microseconds=AGE_US):
        raise ValueError('Health observation is stale at the write checkpoint')
    return now


def current_quality(report,checked_us):
    now=fresh(report,checked_us)
    evidence=deepcopy(report['evidence']);evidence['observed_at']=now.isoformat()
    if quality.evaluate(evidence)['status']!='healthy':
        raise ValueError('Market data is unhealthy at the write checkpoint')


def decision(approved,request,risk_gate,report,checked_us):
    approved=validate_policy(approved);request=contract.validate(request);report=quality.verify(report)
    if (not isinstance(risk_gate,dict) or risk_gate.get('version')!=controls.VERSION
        or type(risk_gate.get('control_revision')) is not int or risk_gate['control_revision']<approved['enrolled_control_revision']
        or risk_gate.get('external_submission_allowed') is not False or risk_gate.get('phase') not in approved['phases']
        or risk_gate.get('request_id')!=request['request_id'] or risk_gate.get('account_id')!=approved['account_id']
        or request['account_id']!=approved['account_id'] or risk_gate.get('decision')!='ALLOW_LOCAL_PAPER'
        or report['instrument_id']!=approved['instrument_id'] or report['status']!='healthy'):
        raise ValueError('Health or controlled request identity denies new work')
    current_quality(report,checked_us)
    rules=report['evidence']['instrument']
    if any(contract.decimal(request['rules'][key])!=contract.decimal(rules[key]) for key in contract.RULE_KEYS):
        raise ValueError('Frozen request rules differ from observed health rules')
    body={'version':GATE_VERSION,'policy':approved,'request':deepcopy(request),'risk_gate':deepcopy(risk_gate),
          'quality':report,'checked_us':checked_us,'phase':risk_gate['phase'],
          'decision':'ALLOW_LOCAL_PAPER_HEALTH','external_submission_allowed':False}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>quality.MAX_BYTES:raise ValueError('Health gate exceeds 256 KiB')
    return result


def verify_gate(value):
    keys={'version','policy','request','risk_gate','quality','checked_us','phase','decision','external_submission_allowed','sha256'}
    if not isinstance(value,dict) or set(value)!=keys or value['version']!=GATE_VERSION or len(encoded(value).encode())>quality.MAX_BYTES:
        raise ValueError('Invalid health gate envelope')
    expected=decision(value['policy'],value['request'],value['risk_gate'],value['quality'],value['checked_us'])
    if encoded(value)!=encoded(expected):raise ValueError('Health gate differs from historical replay')
    return expected


def check(db,row,financial,controlled):
    policy_size=db.scalar(text('SELECT COALESCE(max(octet_length(payload::text)),0) FROM requested_paper_health_policies WHERE account_id=:account'),{'account':row.account_id})
    if policy_size>quality.MAX_SNAPSHOT_BYTES:raise ValueError('Stored health policy exceeds 32 KiB')
    approved=db.get(Policy,row.account_id)
    size=db.scalar(text('SELECT COALESCE(sum(octet_length(payload::text)),0) FROM requested_paper_health_gates WHERE account_id=:account'),{'account':row.account_id})
    if size>MAX_STORED_BYTES:raise ValueError('Stored health evidence exceeds 32 MiB')
    saved=list(db.scalars(select(Gate).where(Gate.account_id==row.account_id).limit(2*journal.REQUEST_LIMIT+1)))
    if row.health_version is None:
        if approved is not None or saved:raise ValueError('Orphan undeclared health evidence')
        return None
    if row.health_version!=POLICY_VERSION or approved is None or controlled['coverage']!='CONTROLLED':
        raise ValueError('Missing declared health policy/control coverage')
    value=validate_policy(approved.payload)
    if (approved.payload_sha256!=sha(value) or value['account_id']!=row.account_id
        or value['opening_sha256']!=sha(financial['opening']) or value['instrument_id']!=financial['opening']['instrument_id']
        or value['enrolled_control_revision']>controlled['revision']):raise ValueError('Health policy storage identity differs')
    replay(row.health_version,value,[deepcopy(stored.payload) for stored in sorted(saved,key=lambda gate:(gate.request_id,gate.phase))],financial,controlled)
    for stored in saved:
        if (stored.payload_sha256!=stored.payload['sha256'] or stored.request_id!=stored.payload['request']['request_id']
            or stored.phase!=stored.payload['phase']):raise ValueError('Health gate storage identity differs')
    return value


def replay(marker,approved,saved,financial,controlled):
    if not isinstance(saved,list) or len(saved)>2*journal.REQUEST_LIMIT:
        raise ValueError('Invalid bounded health evidence list')
    if marker is None:
        if approved is not None or saved:raise ValueError('Orphan undeclared health evidence')
        return None
    if marker!=POLICY_VERSION or controlled['coverage']!='CONTROLLED':
        raise ValueError('Missing declared health/control coverage')
    value=validate_policy(approved)
    if (value['account_id']!=financial['opening']['account_id'] or value['opening_sha256']!=sha(financial['opening'])
        or value['instrument_id']!=financial['opening']['instrument_id'] or value['enrolled_control_revision']>controlled['revision']):
        raise ValueError('Health policy differs from opening/control history')
    expected={(entry['request']['request_id'],phase):(entry,gate) for entry in financial['requests']
              for phase in ['PREPARE','SUBMIT']
              for gate in controlled['gates'] if gate['request_id']==entry['request']['request_id'] and gate['phase']==phase}
    if len(saved)!=len(expected):raise ValueError('Incomplete mandatory health gates')
    order=[]
    for item in saved:
        gate=verify_gate(item);key=(gate['request']['request_id'],gate['phase']);order.append(key)
        context=expected.pop(key,None)
        if context is None:raise ValueError('Orphan or duplicate health gate')
        entry,risk=context
        if (encoded(gate['policy'])!=encoded(value) or encoded(gate['request'])!=encoded(entry['request'])
            or encoded(gate['risk_gate'])!=encoded(risk)):raise ValueError('Health gate request/control checkpoint differs')
    if expected or order!=sorted(order):raise ValueError('Missing or unordered health gates')
    return value


EXPORT_VERSION='paper-requested-health-export-v1'


def capture(engine,account_id):
    with journal.transaction(engine) as db:
        db.execute(text("SET LOCAL lock_timeout='2000ms'"))
        row=journal.lock(db,account_id);financial=journal.audit(db,row);controlled=controls.check(db,row,financial)
        return snapshot(db,row,financial,controlled)


def snapshot(db,row,financial,controlled):
    approved=check(db,row,financial,controlled)
    saved=[deepcopy(g.payload) for g in db.scalars(select(Gate).where(Gate.account_id==row.account_id).order_by(Gate.request_id,Gate.phase))]
    body={'version':EXPORT_VERSION,'declared_version':row.health_version,'policy':approved,'gates':saved,
          'journal':inspection.seal(financial),'controls':controlled,'external_submission_allowed':False}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>MAX_STORED_BYTES:raise ValueError('Health export exceeds 32 MiB')
    verify(result)
    return result


def verify(report):
    keys={'version','declared_version','policy','gates','journal','controls','external_submission_allowed','sha256'}
    if (not isinstance(report,dict) or set(report)!=keys or report['version']!=EXPORT_VERSION
        or len(encoded(report).encode())>MAX_STORED_BYTES):raise ValueError('Invalid bounded health export')
    financial=inspection.verify(report['journal']);view=report['controls']
    if not isinstance(view,dict):raise ValueError('Invalid control export')
    marker=controls.VERSION if view.get('coverage')=='CONTROLLED' else None if view.get('coverage')=='LEGACY_UNMANAGED' else 'unsupported'
    controlled=controls.replay(marker,view.get('policy'),view.get('records'),view.get('gates'),financial)
    approved=replay(report['declared_version'],report['policy'],report['gates'],financial,controlled)
    body={'version':EXPORT_VERSION,'declared_version':report['declared_version'],'policy':approved,'gates':report['gates'],
          'journal':report['journal'],'controls':controlled,'external_submission_allowed':False}
    if encoded(report)!=encoded({**body,'sha256':sha(body)}):raise ValueError('Health export differs from historical replay')
    return deepcopy(report)


def enroll(engine,account_id,expected_control_revision):
    with journal.transaction(engine) as db:
        db.execute(text("SET LOCAL lock_timeout='2000ms'"))
        row=journal.lock(db,account_id);financial=journal.audit(db,row);controlled=controls.check(db,row,financial)
        if type(expected_control_revision) is not int:raise ValueError('Explicit control checkpoint required')
        old=check(db,row,financial,controlled)
        if old is not None:
            if expected_control_revision!=old['enrolled_control_revision']:raise ValueError('Conflicting health enrollment retry')
            return old
        if controlled['coverage']!='CONTROLLED' or controlled['revision']!=expected_control_revision or financial['revision']!=0:
            raise ValueError('Health enrollment requires an untouched controlled account checkpoint')
        value=policy(financial['opening'],expected_control_revision)
        db.add(Policy(account_id=row.account_id,payload=value,payload_sha256=sha(value)));db.flush()
        row.health_version=POLICY_VERSION;db.flush();journal.audit(db,row)
        return value


def guard(db,row,financial,request,risk_gate,cache):
    if row.health_version is None:return None
    if cache is None or risk_gate is None:raise ValueError('Explicit health capture required for enrolled new work')
    approved=db.get(Policy,row.account_id)
    if approved is None:raise ValueError('Missing declared health policy')
    report=quality.capture(db.get_bind(),cache,approved.payload['symbol'])
    from core.paper.requested_ownership import clock
    return decision(approved.payload,request,risk_gate.payload,report,clock(db))


def insert(db,row,value):
    if value is not None:
        db.add(Gate(account_id=row.account_id,request_id=value['request']['request_id'],phase=value['phase'],payload=value,payload_sha256=value['sha256']))


def before_commit(db,value):
    if value is not None:
        from core.paper.requested_ownership import clock
        current_quality(value['quality'],clock(db))
