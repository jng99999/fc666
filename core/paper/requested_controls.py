"""Opt-in local Paper control/risk gates. No identity or external dispatch."""
from copy import deepcopy
from decimal import localcontext
from sqlalchemy import select, text
from core.paper import requested_journal as journal, requested_execution as contract, requested_inspection as inspection
from core.paper.fault_adapter import encoded, sha
from core.storage.models import RequestedPaperPolicyRecord as Policy, RequestedPaperControlRecord as Control, RequestedPaperGateRecord as Gate, RequestedPaperRequestRecord as Request

VERSION='paper-requested-controls-v1'
POLICY_VERSION='paper-requested-risk-v1'
EXPORT_VERSION='paper-requested-controls-export-v1'
EXPORT_VERSION_V2='paper-requested-controls-export-v2'
CONTROL_LIMIT=1000
LIMITS={'max_order_quantity','max_request_notional','max_buy_inventory_quantity','max_fee_rate','min_cash_after_buy','allowed_sides'}


def policy(account_id,limits):
    if not isinstance(account_id,str) or not account_id or len(account_id)>128 or not isinstance(limits,dict) or set(limits)!=LIMITS:
        raise ValueError('Exact explicit account risk limits required')
    sides=limits['allowed_sides']
    if not isinstance(sides,list) or any(s not in ['BUY','SELL'] for s in sides) or len(sides)!=len(set(sides)):
        raise ValueError('Invalid allowed sides')
    for key in LIMITS-{'allowed_sides'}:
        value=contract.decimal(limits[key],asset=key in ['max_order_quantity','max_buy_inventory_quantity','max_fee_rate'],positive=key in ['max_order_quantity','max_request_notional'])
        if key=='max_fee_rate' and value>1:raise ValueError('Policy fee ceiling exceeds one')
    return {'version':POLICY_VERSION,'account_id':account_id,**deepcopy(limits),'allowed_sides':sorted(sides)}


def validate_policy(value):
    if not isinstance(value,dict) or set(value)!={'version','account_id'}|LIMITS:raise ValueError('Invalid policy envelope')
    expected=policy(value['account_id'],{key:value[key] for key in LIMITS})
    if encoded(value)!=encoded(expected):raise ValueError('Unsupported policy/version')
    return expected


def timeline(financial):
    # Offline replay verifies the financial transcript independently first.
    inspection.seal(financial)
    points={0:{'active':None,'clock':financial['opening']['created_at']}}
    revision=0;requests=[]
    for entry in financial['requests']:
        before=revision;revision+=1;value=entry['request'];points[revision]={'active':value['request_id'],'clock':value['created_at']}
        for index,event in enumerate(entry['events']):
            revision+=1;summary=contract.reduce(value,entry['events'][:index+1])
            points[revision]={'active':None if summary['local_source_sealed'] else value['request_id'],'clock':event['received_at']}
        if entry.get('void') is not None:
            revision+=1;points[revision]={'active':None,'clock':entry['void']['created_at']}
        requests.append((entry,before))
    return points,requests


def transition(state,action):
    if action=='RESUME' and state in ['PAUSED','HALTED']:return 'ACTIVE'
    if action=='PAUSE' and state in ['ACTIVE','HALTED']:return 'PAUSED'
    if action=='HALT' and state=='ACTIVE':return 'HALTED'
    if action=='STOP' and state in ['ACTIVE','PAUSED','HALTED']:return 'STOPPED'
    raise ValueError('Invalid/terminal control transition')


def record(account_id,sequence,command_id,action,journal_revision,created_at,policy_sha256,state):
    if not isinstance(command_id,str) or not command_id or len(command_id)>128:raise ValueError('Bounded stable command ID required')
    contract.clock(created_at)
    return {'version':VERSION,'account_id':account_id,'sequence':sequence,'command_id':command_id,'action':action,
            'expected_revision':sequence,'journal_revision':journal_revision,'created_at':created_at,'policy_sha256':policy_sha256,
            'from_state':state,'to_state':'PAUSED' if sequence==0 and action=='ENROLL' and state is None else transition(state,action)}


def decision(value,approved,control,phase,before,observed_at):
    if phase not in ['PREPARE','SUBMIT'] or contract.clock(observed_at)<contract.clock(control['created_at']):
        raise ValueError('Admission predates active control')
    state=control['to_state'];side=value['side']
    if state not in ['ACTIVE','HALTED'] or (state=='HALTED' and side=='BUY') or side not in approved['allowed_sides']:
        raise ValueError('Account control or allowed-side gate denies new work')
    with localcontext() as context:
        context.prec=260
        size=contract.decimal(value['requested_quantity'],asset=True);price=contract.decimal(value['limit_price'],asset=True)
        rate=contract.decimal(value['max_fee_rate'],asset=True);base=contract.base_values(value['base'])
        cash,_=contract.reserve(side,size,price,rate);notional=size*price
        if (size>contract.decimal(approved['max_order_quantity'],asset=True) or notional>contract.decimal(approved['max_request_notional']) or
            rate>contract.decimal(approved['max_fee_rate'],asset=True) or (side=='BUY' and (
                base['quantity']+size>contract.decimal(approved['max_buy_inventory_quantity'],asset=True) or
                base['cash']-cash<contract.decimal(approved['min_cash_after_buy'])))):
            raise ValueError('Frozen full request exceeds explicit local risk limits')
    return {'version':VERSION,'account_id':value['account_id'],'request_id':value['request_id'],'phase':phase,
            'policy_sha256':sha(approved),'control_revision':control['sequence']+1,'control_sha256':sha(control),
            'journal_revision':before,'observed_at':observed_at,'state':state,'requested_quantity':value['requested_quantity'],
            'request_notional':str(notional),'reserved_cash':str(cash),'decision':'ALLOW_LOCAL_PAPER','external_submission_allowed':False}


def replay(marker,approved,records,gates,financial):
    if marker is None:
        if approved is not None or records or gates:raise ValueError('Undeclared control evidence')
        return {'version':VERSION,'coverage':'LEGACY_UNMANAGED','policy':None,'records':[],'gates':[],
                'state':None,'revision':None,'enrolled_journal_revision':None}
    if marker!=VERSION:raise ValueError('Unsupported declared control version')
    approved=validate_policy(approved)
    account_id=financial['opening']['account_id']
    if approved['account_id']!=account_id or not isinstance(records,list) or not 1<=len(records)<=CONTROL_LIMIT or not isinstance(gates,list) or len(gates)>2*journal.REQUEST_LIMIT:
        raise ValueError('Missing/invalid bounded control evidence')
    points,requests=timeline(financial)
    state=None;clock=None;checkpoint=-1;identities=set()
    for sequence,value in enumerate(records):
        if not isinstance(value,dict):raise ValueError('Invalid control record')
        point=value.get('journal_revision')
        if type(point) is not int or point not in points or point<checkpoint or value.get('command_id') in identities:
            raise ValueError('Invalid control financial checkpoint/identity')
        expected=record(account_id,sequence,value.get('command_id'),value.get('action'),point,value.get('created_at'),sha(approved),state)
        if encoded(value)!=encoded(expected) or (sequence==0 and (value['action']!='ENROLL' or points[point]['active'] is not None)):
            raise ValueError('Control chain mismatch')
        now=contract.clock(value['created_at'])
        if now<contract.clock(points[point]['clock']) or (clock is not None and now<clock):raise ValueError('Control clock regression')
        clock=now;checkpoint=point;state=value['to_state'];identities.add(value['command_id'])
    enrolled=records[0]['journal_revision'];expected_gates=[]
    for entry,before in requests:
        if before<enrolled:continue
        value=entry['request']
        for phase,point,observed in [('PREPARE',before,value['created_at']),*([('SUBMIT',before+1,entry['events'][0]['received_at'])] if entry['events'] else [])]:
            candidates=[control for control in records if control['journal_revision']<=point]
            if not candidates:raise ValueError('Request lacks control origin')
            expected_gates.append(decision(value,approved,candidates[-1],phase,point,observed))
    for entry,before in requests:
        if entry.get('void') is not None and before>=enrolled:
            effective=[control for control in records if control['journal_revision']<=before+1][-1]
            if contract.clock(entry['void']['created_at'])<contract.clock(effective['created_at']):raise ValueError('Finalization predates control')
    if encoded(gates)!=encoded(expected_gates):raise ValueError('Admission evidence missing or differs from replay')
    result={'version':VERSION,'coverage':'CONTROLLED','policy':deepcopy(approved),'records':deepcopy(records),'gates':deepcopy(gates),
            'state':state,'revision':len(records),'enrolled_journal_revision':enrolled}
    if len(encoded(result).encode())>contract.MAX_BYTES:raise ValueError('Controls exceed 32 MiB')
    return result


def check(db,row,financial):
    stored=db.get(Policy,row.account_id)
    records=list(db.scalars(select(Control).where(Control.account_id==row.account_id).order_by(Control.sequence).limit(CONTROL_LIMIT+1)))
    gates=list(db.scalars(select(Gate).join(Request,Request.request_id==Gate.request_id).where(Request.account_id==row.account_id).order_by(Request.ordinal,Gate.phase).limit(2*journal.REQUEST_LIMIT+1)))
    if stored is not None and stored.payload_sha256!=sha(stored.payload):raise ValueError('Policy evidence mismatch')
    for index,recorded in enumerate(records):
        if recorded.sequence!=index or recorded.command_id!=recorded.payload.get('command_id') or recorded.payload_sha256!=sha(recorded.payload):raise ValueError('Control evidence mismatch')
    for admitted in gates:
        if admitted.request_id!=admitted.payload.get('request_id') or admitted.phase!=admitted.payload.get('phase') or admitted.payload_sha256!=sha(admitted.payload):raise ValueError('Gate evidence mismatch')
    return replay(row.control_version,stored.payload if stored else None,[r.payload for r in records],[g.payload for g in gates],financial)


def gate(db,row,financial,value,phase,observed_at):
    controlled=check(db,row,financial)
    if controlled['coverage']=='LEGACY_UNMANAGED':return None
    payload=decision(value,controlled['policy'],controlled['records'][-1],phase,financial['revision'],observed_at)
    return Gate(request_id=value['request_id'],phase=phase,payload=payload,payload_sha256=sha(payload))


def enroll(engine,account_id,approved,command_id,created_at,*,expected_financial_revision=None):
    approved=validate_policy(approved)
    with journal.transaction(engine) as db:
        if expected_financial_revision is not None:db.execute(text("SELECT set_config('lock_timeout', '2000ms', true)"))
        row=journal.lock(db,account_id);financial=journal.audit(db,row)
        if approved['account_id']!=account_id:raise ValueError('Policy account mismatch')
        old=check(db,row,financial)
        if old['coverage']=='CONTROLLED':
            if (encoded(old['policy'])!=encoded(approved) or old['records'][0]['command_id']!=command_id or old['records'][0]['created_at']!=created_at or
                (expected_financial_revision is not None and (type(expected_financial_revision) is not int or old['records'][0]['journal_revision']!=expected_financial_revision))):
                raise ValueError('Conflicting enrollment retry')
            return old
        if expected_financial_revision is not None and (type(expected_financial_revision) is not int or financial['revision']!=expected_financial_revision):raise ValueError('Financial revision conflict')
        if financial['active_request_id'] is not None:raise ValueError('Cannot enroll an unsealed legacy request')
        value=record(account_id,0,command_id,'ENROLL',financial['revision'],created_at,sha(approved),None)
        db.add(Policy(account_id=account_id,payload=approved,payload_sha256=sha(approved)));db.flush()
        db.add(Control(account_id=account_id,sequence=0,command_id=command_id,payload=value,payload_sha256=sha(value)))
        row.control_version=VERSION;db.flush()
        return check(db,row,financial)


def command(engine,account_id,command_id,expected_revision,action,created_at,*,expected_financial_revision=None):
    with journal.transaction(engine) as db:
        if expected_financial_revision is not None:db.execute(text("SELECT set_config('lock_timeout', '2000ms', true)"))
        row=journal.lock(db,account_id);financial=journal.audit(db,row);view=check(db,row,financial)
        if view['coverage']!='CONTROLLED':raise ValueError('Explicit policy enrollment required')
        existing=next((r for r in view['records'] if r['command_id']==command_id),None)
        if existing is not None:
            if (existing['expected_revision']!=expected_revision or existing['action']!=action or existing['created_at']!=created_at or type(expected_revision) is not int or
                (expected_financial_revision is not None and (type(expected_financial_revision) is not int or existing['journal_revision']!=expected_financial_revision))):
                raise ValueError('Conflicting command retry')
            return view
        if type(expected_revision) is not int or expected_revision!=view['revision']:raise ValueError('Control revision conflict')
        if expected_financial_revision is not None and (type(expected_financial_revision) is not int or expected_financial_revision!=financial['revision']):raise ValueError('Financial revision conflict')
        if len(view['records'])>=CONTROL_LIMIT or (action!='STOP' and len(view['records'])>=CONTROL_LIMIT-1):
            raise ValueError('Control history capacity reached; last slot reserved for STOP')
        value=record(account_id,len(view['records']),command_id,action,financial['revision'],created_at,sha(view['policy']),view['state'])
        db.add(Control(account_id=account_id,sequence=len(view['records']),command_id=command_id,payload=value,payload_sha256=sha(value)));db.flush()
        return check(db,row,financial)


def read(engine,account_id):
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout', '2000ms', true)"))
        row=journal.lock(db,account_id);financial=journal.audit(db,row)
        return financial,check(db,row,financial)


def capture(engine,account_id):
    financial,view=read(engine,account_id)
    body={'version':EXPORT_VERSION_V2 if financial['version']==journal.VERSION_V2 else EXPORT_VERSION,'journal':inspection.seal(financial),'controls':view}
    report={**body,'sha256':sha(body)}
    if len(encoded(report).encode())>contract.MAX_BYTES:raise ValueError('Control export exceeds 32 MiB')
    return report


def verify(report):
    if not isinstance(report,dict) or set(report)!={'version','journal','controls','sha256'} or report['version'] not in [EXPORT_VERSION,EXPORT_VERSION_V2] or len(encoded(report).encode())>contract.MAX_BYTES:
        raise ValueError('Invalid control export envelope/bounds')
    financial=inspection.verify(report['journal']);view=report['controls']
    if not isinstance(view,dict):raise ValueError('Invalid control view')
    marker=VERSION if view.get('coverage')=='CONTROLLED' else None if view.get('coverage')=='LEGACY_UNMANAGED' else 'unsupported'
    expected=replay(marker,view.get('policy'),view.get('records'),view.get('gates'),financial)
    body={'version':EXPORT_VERSION_V2 if financial['version']==journal.VERSION_V2 else EXPORT_VERSION,'journal':report['journal'],'controls':expected}
    if encoded(report)!=encoded({**body,'sha256':sha(body)}):raise ValueError('Control export differs from replay')
    return deepcopy(expected)
