"""Segment-scoped sampled equity metrics; never infer continuous drawdown."""
import json
from decimal import Decimal,localcontext
from core.portfolio import continuity

VERSION='paper-portfolio-sampled-v1'


def measure(points):
    if not points:return None
    with localcontext() as ctx:
        ctx.prec=120
        peak=Decimal(points[0]['equity']);peak_id=points[0]['snapshot_id']
        maximum=Decimal(0);maximum_ratio=None;amount_pair=None;ratio_pair=None
        for point in points:
            equity=Decimal(point['equity'])
            if not equity.is_finite() or equity<0:raise ValueError('Finite nonnegative equity required')
            if equity>peak:peak=equity;peak_id=point['snapshot_id']
            drawdown=peak-equity;ratio=drawdown/peak if peak>0 else None
            if drawdown>maximum:
                maximum=drawdown;amount_pair={'peak_snapshot_id':peak_id,'trough_snapshot_id':point['snapshot_id']}
            if ratio is not None and (maximum_ratio is None or ratio>maximum_ratio):
                maximum_ratio=ratio;ratio_pair={'peak_snapshot_id':peak_id,'trough_snapshot_id':point['snapshot_id']} if ratio>0 else None
        insufficient=len(points)<2
        return {'observations':len(points),'measurement_status':'INSUFFICIENT_OBSERVATIONS' if insufficient else 'OBSERVED_POINTS',
            'peak_equity':continuity.amount(peak),
            'max_drawdown_amount':None if insufficient else continuity.amount(maximum),
            'max_drawdown_ratio':None if insufficient or maximum_ratio is None else continuity.amount(maximum_ratio),
            'ratio_unavailable_reason':'INSUFFICIENT_OBSERVATIONS' if insufficient else 'NO_POSITIVE_PEAK' if maximum_ratio is None else None,
            'amount_interval':None if insufficient else amount_pair,'ratio_interval':None if insufficient else ratio_pair}


def evaluate(inputs):
    analysis=continuity.evaluate(inputs)
    points=[];segments=[]
    with localcontext() as ctx:
        ctx.prec=120
        equities=[Decimal(point['equity']) for point in analysis['points'] if point['equity'] is not None]
        low=min(equities) if equities else None;high=max(equities) if equities else None
        for index,point in enumerate(analysis['points']):
            height=None
            if point['equity'] is not None:
                height=continuity.amount((Decimal(point['equity'])-low)/(high-low)) if high>low else '0.5'
            points.append({**point,'plot_index':index,'plot_height':height})
        for segment in analysis['segments']:
            members=[point for point in points if point['segment_id']==segment['segment_id']]
            segments.append({**segment,'sampled':measure(members)})
    result={'version':VERSION,'scenario_id':analysis['scenario_id'],'mode':'INDEPENDENT_PAPER_SAMPLED_OBSERVATIONS',
        'window':analysis['window'],'points':points,'segments':segments,'edges':analysis['edges'],
        'plot':{'x_axis':'STORAGE_ORDER','low_equity':continuity.amount(low) if low is not None else None,
            'high_equity':continuity.amount(high) if high is not None else None},
        'summary':{**analysis['summary'],'measurable_segments':sum(segment['sampled']['measurement_status']=='OBSERVED_POINTS' for segment in segments)},
        'inputs_sha256':analysis['inputs_sha256'],'inputs':inputs,'trading_enabled':False}
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())>continuity.MAX_BYTES:raise ValueError('Sampled export exceeds 32 MiB')
    return result


def capture(engine,id,*,limit=continuity.MAX_POINTS):
    return evaluate(continuity.capture(engine,id,limit=limit)['inputs'])


def verify(value):
    if value!=evaluate(value['inputs']):raise ValueError('Sampled analysis differs from reproduced inputs')
    return value['summary']
