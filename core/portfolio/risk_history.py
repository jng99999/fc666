"""Versioned, hint-only risk observations of immutable saved Paper valuations."""
import json
from decimal import Decimal
from core.backtest.spot import digest
from core.portfolio import continuity

VERSION='paper-portfolio-risk-history-v1'
POLICY_VERSION='paper-portfolio-exposure-hints-v1'


def policy(scenario):
    return {'version':POLICY_VERSION,'mode':'HINT_ONLY','scenario_id':scenario['scenario_id'],
        'definition_sha256':scenario['definition_sha256'],'limits':scenario['definition']['limits'],
        'comparison':'STRICT_GT','assets':['BTC','ETH'],'quote':'USDT','trading_enabled':False}


def rules(report,limits):
    totals=report['totals'];available=report['status']=='COMPLETE' and totals is not None and Decimal(totals['equity'])>0
    weights={item['base']:item['weight'] for item in report['exposures']}
    values=[('GROSS_WEIGHT',None,totals['gross_weight'] if available else None,limits['max_gross_weight'])]
    values += [('ASSET_WEIGHT',asset,weights.get(asset,'0') if available else None,limits['max_asset_weight']) for asset in ['BTC','ETH']]
    result=[]
    for code,asset,value,limit in values:
        result.append({'rule_id':code if asset is None else code+':'+asset,'code':code,'asset':asset,'value':value,'limit':limit,
            'status':'UNAVAILABLE' if value is None else 'BREACH' if Decimal(value)>Decimal(limit) else 'NOT_TRIGGERED'})
    return result


def evaluate(inputs):
    analysis=continuity.evaluate(inputs);frozen=policy(inputs['scenario']);points=[];events=[]
    for index,snapshot in enumerate(inputs['snapshots']):
        report=snapshot['report'];evaluated=rules(report,frozen['limits'])
        operational=[item for item in report['alerts'] if item['code'] in ['WORKER_UNAVAILABLE','ENTRY_HALTED']]
        unavailable=[item for item in report['alerts'] if item['code'] not in ['GROSS_WEIGHT','ASSET_WEIGHT','WORKER_UNAVAILABLE','ENTRY_HALTED']]
        unknown=any(rule['status']=='UNAVAILABLE' for rule in evaluated) or any(item['code']=='WORKER_UNAVAILABLE' for item in operational)
        hints=any(rule['status']=='BREACH' for rule in evaluated) or bool(operational)
        edge=analysis['edges'][index-1] if index else None
        context='BASELINE' if edge is None else 'COMPARABLE' if edge['comparable'] else 'RESET'
        transitions=[]
        if context=='COMPARABLE':
            for previous,current in zip(points[-1]['rules'],evaluated):
                if 'UNAVAILABLE' not in [previous['status'],current['status']] and previous['status']!=current['status']:
                    event={'from_snapshot_id':points[-1]['snapshot_id'],'to_snapshot_id':snapshot['snapshot_id'],
                        'rule_id':current['rule_id'],'event':'ENTERED_BREACH' if current['status']=='BREACH' else 'LEFT_BREACH',
                        'previous_value':previous['value'],'value':current['value'],'limit':current['limit']}
                    transitions.append(event);events.append(event)
        points.append({'snapshot_id':snapshot['snapshot_id'],'as_of':report['as_of'],'price_as_of':report['price_as_of'],
            'valuation_status':report['status'],'status':'UNKNOWN' if unknown else 'HINTS_PRESENT' if hints else 'NO_CONFIGURED_HINTS',
            'context':context,'reset_reasons':edge['reasons'] if edge else [],'rules':evaluated,
            'operational_hints':operational,'unavailable_reasons':unavailable,'transitions':transitions})
    result={'version':VERSION,'scenario_id':analysis['scenario_id'],'mode':'HINT_ONLY','policy':frozen,'policy_sha256':digest(frozen),
        'window':analysis['window'],'points':points,'events':events,
        'summary':{'unknown':sum(point['status']=='UNKNOWN' for point in points),'hints_present':sum(point['status']=='HINTS_PRESENT' for point in points),
            'no_configured_hints':sum(point['status']=='NO_CONFIGURED_HINTS' for point in points),'transitions':len(events),
            'comparable_links':analysis['summary']['comparable_links'],'breaks':analysis['summary']['breaks']},
        'inputs_sha256':analysis['inputs_sha256'],'inputs':inputs,'trading_enabled':False}
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())>continuity.MAX_BYTES:raise ValueError('Risk export exceeds 32 MiB')
    return result


def capture(engine,id,*,limit=continuity.MAX_POINTS):
    return evaluate(continuity.capture(engine,id,limit=limit)['inputs'])


def verify(value):
    if value!=evaluate(value['inputs']):raise ValueError('Risk history differs from reproduced inputs')
    return value['summary']
