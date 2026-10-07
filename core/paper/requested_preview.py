"""Read-only full-quantity funding/admission preview, never a reservation."""
from copy import deepcopy
from core.paper import requested_execution as contract,requested_journal as journal,requested_controls as controls,requested_inspection as inspection
from core.paper.fault_adapter import encoded,sha

VERSION='paper-requested-preparation-preview-v1'
PROPOSAL_KEYS={'account_id','client_request_id','side','requested_quantity','limit_price','max_fee_rate','rules','created_at','expected_financial_revision','expected_control_revision'}


def evaluate(financial,controlled,proposal):
    inspection.seal(financial)
    if not isinstance(controlled,dict) or controlled.get('coverage')!='CONTROLLED':raise ValueError('Explicit controlled account required')
    expected=controls.replay(controls.VERSION,controlled.get('policy'),controlled.get('records'),controlled.get('gates'),financial)
    if encoded(controlled)!=encoded(expected):raise ValueError('Control view differs from replay')
    if not isinstance(proposal,dict) or set(proposal)!=PROPOSAL_KEYS:raise ValueError('Exact proposal envelope required')
    for key,value in [('expected_financial_revision',financial['revision']),('expected_control_revision',controlled['revision'])]:
        if type(proposal[key]) is not int or proposal[key]!=value:raise ValueError('Preview revision conflict')
    if proposal['account_id']!=financial['opening']['account_id']:raise ValueError('Preview account mismatch')
    if financial['active_request_id'] is not None or len(financial['requests'])>=journal.REQUEST_LIMIT:raise ValueError('Account is not available for a new request')
    if any(entry['request']['client_request_id']==proposal['client_request_id'] for entry in financial['requests']):raise ValueError('Preview requires an unused request identity')
    if contract.clock(proposal['created_at'])<contract.clock(financial['last_clock']):raise ValueError('Preview predates account')
    value=contract.request(**{key:proposal[key] for key in PROPOSAL_KEYS-{'expected_financial_revision','expected_control_revision'}},base=financial['account'])
    gate=controls.decision(value,controlled['policy'],controlled['records'][-1],'PREPARE',financial['revision'],proposal['created_at'])
    projected=contract.reduce(value,[])
    body={'version':VERSION,'journal':inspection.seal(financial),'controls':deepcopy(controlled),'proposal':deepcopy(proposal),
          'request':value,'admission':gate,'projected_summary':projected,'reservation_created':False,'external_submission_allowed':False}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>contract.MAX_BYTES:raise ValueError('Preview exceeds 32 MiB')
    return result


def capture(engine,proposal):
    if not isinstance(proposal,dict) or set(proposal)!=PROPOSAL_KEYS:raise ValueError('Exact proposal envelope required')
    financial,controlled=controls.read(engine,proposal['account_id'])
    return evaluate(financial,controlled,proposal)


def verify(report):
    keys={'version','journal','controls','proposal','request','admission','projected_summary','reservation_created','external_submission_allowed','sha256'}
    if not isinstance(report,dict) or set(report)!=keys or report['version']!=VERSION or len(encoded(report).encode())>contract.MAX_BYTES:raise ValueError('Invalid preview envelope/version/bounds')
    expected=evaluate(inspection.verify(report['journal']),report['controls'],report['proposal'])
    if encoded(report)!=encoded(expected):raise ValueError('Preview differs from complete offline replay')
    return deepcopy(expected)
