"""Bounded, reproducible comparisons of saved independent Paper valuations."""
import json
from decimal import Decimal,localcontext
from sqlalchemy import select
from sqlalchemy.orm import Session
from core.backtest.spot import digest
from core.portfolio import history
from core.portfolio.valuation import stamp,MissingAccount
from core.storage.models import PortfolioScenarioRecord as Scenario,PortfolioSnapshotRecord as Snapshot

VERSION='paper-portfolio-continuity-v1'
MAX_POINTS=8
MAX_BYTES=32*1024*1024


def amount(value):
    if value==0:return '0'
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def reasons(left,right):
    a,b=left['report'],right['report'];result=[]
    if a['status']!='COMPLETE' or b['status']!='COMPLETE':result.append('UNAVAILABLE_VALUATION')
    if stamp(b['as_of'])<=stamp(a['as_of']):result.append('READ_CLOCK_NOT_ADVANCING')
    seconds=int((stamp(b['price_as_of'])-stamp(a['price_as_of'])).total_seconds())
    if seconds<=0:result.append('PRICE_CLOCK_NOT_ADVANCING')
    elif seconds!=60:result.append('OBSERVATION_GAP')
    if any(item['code']=='WORKER_UNAVAILABLE' for report in [a,b] for item in report['alerts']):result.append('WORKER_HEALTH_WARNING')
    for previous,current in zip(a['inputs']['accounts'],b['inputs']['accounts']):
        old,new=previous['record'],current['record']
        if old['snapshot']!=new['snapshot']:result.append('ACCOUNT_DEFINITION_CHANGED')
        if previous['instrument']!=current['instrument']:result.append('INSTRUMENT_RULES_CHANGED')
        if new['revision']<old['revision']:result.append('ACCOUNT_REVISION_REGRESSED')
        if new['observations'][:len(old['observations'])]!=old['observations']:result.append('OBSERVATION_PREFIX_CHANGED')
        if current['accepted'][:len(previous['accepted'])]!=previous['accepted']:result.append('ACCEPTED_DATA_PREFIX_CHANGED')
        if any(new['ledger'][field][:len(old['ledger'][field])]!=old['ledger'][field] for field in ['orders','fills','equity','signals']):
            result.append('LEDGER_PREFIX_CHANGED')
    return sorted(set(result))


def evaluate(inputs):
    if set(inputs)!={'version','limit','has_earlier','scenario','snapshots'} or inputs['version']!=VERSION:raise ValueError('Invalid continuity inputs')
    limit=inputs['limit']
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=MAX_POINTS:raise ValueError('Point limit must be 1..8')
    if not isinstance(inputs['has_earlier'],bool):raise ValueError('Invalid window boundary')
    saved=inputs['snapshots']
    if not isinstance(saved,list) or len(saved)>limit:raise ValueError('Invalid window size')
    scenario=inputs['scenario'];history.validate_scenario_export(scenario)
    seen=set();previous_order=None
    for value in saved:
        history.verify_export(value)
        if value['scenario']!=scenario:raise ValueError('Mixed scenario inputs')
        order=(stamp(value['created_at']),value['snapshot_id'])
        if value['snapshot_id'] in seen or previous_order is not None and order<=previous_order:raise ValueError('Duplicate or unordered snapshots')
        seen.add(value['snapshot_id']);previous_order=order
    if len(json.dumps(inputs,ensure_ascii=False,separators=(',',':')).encode())>MAX_BYTES:raise ValueError('Continuity export exceeds 32 MiB')
    points=[];edges=[];segments=[];segment=None
    with localcontext() as ctx:
        ctx.prec=120
        for index,value in enumerate(saved):
            report=value['report'];issues=[] if index==0 else reasons(saved[index-1],value)
            comparable=index>0 and not issues
            delta=None
            if comparable:
                delta=amount(Decimal(report['totals']['equity'])-Decimal(saved[index-1]['report']['totals']['equity']))
            if index:
                elapsed=int((stamp(report['price_as_of'])-stamp(saved[index-1]['report']['price_as_of'])).total_seconds())
                edges.append({'from_snapshot_id':saved[index-1]['snapshot_id'],'to_snapshot_id':value['snapshot_id'],
                    'comparable':comparable,'reasons':issues,'price_elapsed_seconds':elapsed,
                    'unobserved_minutes':max(elapsed//60-1,0),'equity_change':delta})
            if report['status']=='COMPLETE':
                if not comparable:
                    segment={'segment_id':value['snapshot_id'],'first_snapshot_id':value['snapshot_id'],
                        'last_snapshot_id':value['snapshot_id'],'observations':0,'first_equity':report['totals']['equity'],
                        'last_equity':report['totals']['equity'],'equity_change':'0'}
                    segments.append(segment)
                segment['last_snapshot_id']=value['snapshot_id'];segment['observations']+=1
                segment['last_equity']=report['totals']['equity']
                segment['equity_change']=amount(Decimal(segment['last_equity'])-Decimal(segment['first_equity']))
            else:segment=None
            points.append({'snapshot_id':value['snapshot_id'],'created_at':value['created_at'],'as_of':report['as_of'],
                'price_as_of':report['price_as_of'],'status':report['status'],'equity':report['totals']['equity'] if report['totals'] else None,
                'report_sha256':value['report_sha256'],'segment_id':segment['segment_id'] if segment else None})
    result={'version':VERSION,'scenario_id':scenario['scenario_id'],'mode':'INDEPENDENT_PAPER_OBSERVATIONS',
        'window':{'limit':limit,'has_earlier':inputs['has_earlier'],'order':'STORAGE_TIME_ASC','points':len(points)},
        'points':points,'edges':edges,'segments':segments,
        'summary':{'complete':sum(point['status']=='COMPLETE' for point in points),'unavailable':sum(point['status']=='UNAVAILABLE' for point in points),
            'comparable_links':sum(edge['comparable'] for edge in edges),'breaks':sum(not edge['comparable'] for edge in edges)},
        'inputs_sha256':digest(inputs),'inputs':inputs,'trading_enabled':False}
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())>MAX_BYTES:
        raise ValueError('Continuity export exceeds 32 MiB')
    return result


def capture(engine,id,*,limit=MAX_POINTS):
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=MAX_POINTS:raise ValueError('Point limit must be 1..8')
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin(),Session(bind=conn) as db:
        scenario=db.get(Scenario,id)
        if scenario is None:raise MissingAccount()
        rows=list(db.scalars(select(Snapshot).where(Snapshot.scenario_id==id).order_by(Snapshot.created_at.desc(),Snapshot.snapshot_id.desc()).limit(limit+1)))
        inputs={'version':VERSION,'limit':limit,'has_earlier':len(rows)>limit,'scenario':history.scenario_visible(scenario),
            'snapshots':[history.snapshot_visible(row,scenario) for row in reversed(rows[:limit])]}
        return evaluate(inputs)


def verify(value):
    if value!=evaluate(value['inputs']):raise ValueError('Continuity analysis differs from reproduced inputs')
    return value['summary']
