"""Fixed-allocation local quote-capital forecast; no shared reservation writes."""
from copy import deepcopy
from decimal import localcontext
import re
from core.paper import requested_execution as execution,requested_inspection as inspection
from core.paper.fault_adapter import encoded,sha

VERSION='paper-shared-quote-capital-preview-v1'
POOL_KEYS={'pool_id','quote_asset','opening_quote_cash','max_reserved_quote','allocations'}
ALLOCATION_KEYS={'account_id','instrument_id','opening_quote_cash'}
CANDIDATE_KEYS={'account_id','client_request_id','side','requested_quantity','limit_price','max_fee_rate','rules','created_at'}
LIMIT=100


def validate_pool(pool):
    if not isinstance(pool,dict) or set(pool)!=POOL_KEYS:raise ValueError('Exact pool definition required')
    if type(pool['pool_id']) is not str or not 1<=len(pool['pool_id'])<=128 or type(pool['quote_asset']) is not str or re.fullmatch('[A-Z0-9]{2,16}',pool['quote_asset']) is None:raise ValueError('Invalid pool identity/quote asset')
    opening=execution.decimal(pool['opening_quote_cash']);maximum=execution.decimal(pool['max_reserved_quote'])
    rows=pool['allocations']
    if not isinstance(rows,list) or not 1<=len(rows)<=LIMIT:raise ValueError('Bounded pool membership required')
    known=set();allocated=execution.decimal('0')
    for row in rows:
        if not isinstance(row,dict) or set(row)!=ALLOCATION_KEYS:raise ValueError('Exact allocation required')
        if any(type(row[key]) is not str or not 1<=len(row[key])<=128 for key in ('account_id','instrument_id')) or row['account_id'] in known:raise ValueError('Duplicate/invalid pool account')
        known.add(row['account_id']);allocated+=execution.decimal(row['opening_quote_cash'])
    if allocated>opening or maximum>opening:raise ValueError('Opening allocation/reservation cap exceeds pool')
    if rows!=sorted(rows,key=lambda row:row['account_id']):raise ValueError('Canonical account order required')
    return opening,allocated,maximum


def evaluate(pool,journal_reports,candidate=None):
    if len(encoded({'pool':pool,'journals':journal_reports,'candidate':candidate}).encode())>execution.MAX_BYTES:raise ValueError('Shared capital input exceeds32MiB')
    with localcontext() as context:
        context.prec=260
        opening,allocated,maximum=validate_pool(pool)
        if not isinstance(journal_reports,list) or len(journal_reports)!=len(pool['allocations']):raise ValueError('Complete pool membership evidence required')
        accounts=[];financials={};cash_total=opening-allocated;reserved_total=execution.decimal('0')
        for allocation,report in zip(pool['allocations'],journal_reports):
            financial=inspection.verify(report);seed=financial['opening'];account=financial['account']
            if seed['account_id']!=allocation['account_id'] or seed['instrument_id']!=allocation['instrument_id'] or execution.decimal(seed['base']['cash'])!=execution.decimal(allocation['opening_quote_cash']):raise ValueError('Journal opening differs from fixed allocation')
            if any(execution.decimal(seed['base'][key],signed=key=='realized_pnl')!=0 for key in ('quantity','cost_basis','fees','realized_pnl')):raise ValueError('Quote-only pool opening required')
            active=next((entry for entry in financial['requests'] if entry['request']['request_id']==financial['active_request_id']),None)
            reserved=execution.decimal(active['summary']['funding']['reserved_cash']) if active else execution.decimal('0')
            cash=execution.decimal(account['cash'])
            if reserved>cash:raise ValueError('Reservation exceeds account cash')
            cash_total+=cash;reserved_total+=reserved
            accounts.append({'account_id':seed['account_id'],'instrument_id':seed['instrument_id'],'financial_revision':financial['revision'],
                             'current_quote_cash':str(cash),'reserved_quote':str(reserved),'available_quote_cash':str(cash-reserved),
                             'active_request_id':financial['active_request_id'],'journal_sha256':report['sha256']})
            financials[seed['account_id']]=financial
        projected=reserved_total;forecast=None
        if candidate is not None:
            if not isinstance(candidate,dict) or set(candidate)!=CANDIDATE_KEYS or candidate['account_id'] not in financials:raise ValueError('Exact candidate for pool account required')
            financial=financials[candidate['account_id']]
            if financial['active_request_id'] is not None:raise ValueError('Account already has an active request')
            if any(entry['request']['client_request_id']==candidate['client_request_id'] for entry in financial['requests']):raise ValueError('Candidate requires unused identity')
            if execution.clock(candidate['created_at'])<execution.clock(financial['last_clock']):raise ValueError('Candidate predates financial checkpoint')
            request=execution.request(**candidate,base=financial['account']);summary=execution.reduce(request,[])
            added=execution.decimal(summary['funding']['reserved_cash']);projected+=added
            allowed=candidate['side']=='BUY' and projected<=maximum
            forecast={'request':request,'additional_reserved_quote':str(added),'projected_reserved_quote':str(projected),
                      'capital_forecast_allowed':allowed,'reason':'SELL_SHARED_INVENTORY_NOT_IMPLEMENTED' if candidate['side']!='BUY' else 'WITHIN_QUOTE_RESERVATION_CAP' if allowed else 'QUOTE_RESERVATION_CAP_EXCEEDED'}
        body={'version':VERSION,'pool':deepcopy(pool),'journals':deepcopy(journal_reports),'candidate':deepcopy(candidate),
              'accounts':accounts,'unallocated_opening_quote':str(opening-allocated),'current_quote_cash':str(cash_total),
              'reserved_quote':str(reserved_total),'available_quote_cash':str(cash_total-reserved_total),
              'reservation_cap_breached':reserved_total>maximum,'forecast':forecast,
              'scope':'FIXED_ALLOCATION_LOCAL_QUOTE_FORECAST','coherent_capture_supported':False,'shared_reservation_committed':False,
              'transfers_supported':False,'exclusive_pool_membership_supported':False,'submission_allowed':False,'read_only':True}
        result={**body,'sha256':sha(body)}
        if len(encoded(result).encode())>execution.MAX_BYTES:raise ValueError('Shared capital report exceeds32MiB')
        return result


def verify(report):
    keys={'version','pool','journals','candidate','accounts','unallocated_opening_quote','current_quote_cash','reserved_quote','available_quote_cash',
          'reservation_cap_breached','forecast','scope','coherent_capture_supported','shared_reservation_committed','transfers_supported','exclusive_pool_membership_supported','submission_allowed','read_only','sha256'}
    if not isinstance(report,dict) or set(report)!=keys or report['version']!=VERSION or len(encoded(report).encode())>execution.MAX_BYTES:raise ValueError('Invalid shared capital report')
    expected=evaluate(report['pool'],report['journals'],report['candidate'])
    if encoded(expected)!=encoded(report):raise ValueError('Shared capital differs from complete replay')
    return expected
