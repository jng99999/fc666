"""Freeze public history and reproduce closed-rule PAPER diagnostics, never orders."""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from core.backtest.price_action import PriceActionBacktestConfig, simulate
from core.backtest.spot import digest
from core.exchange.bitget import BitgetPublic
from core.paper.price_action_rules import INTERVALS, RuleBar

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT/'docs/closed-rules-backtest-protocol.json'
SOURCES = ('core/backtest/price_action.py', 'core/paper/price_action_rules.py',
           'core/paper/directional.py', 'scripts/study_closed_rules.py', 'scripts/freeze_gold_rule_history.py')


def write_once(path, value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError('Existing immutable study artifact differs: '+path.name)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        handle.write(encoded)


def load_history(path):
    if path.stat().st_size > 10000000:
        raise ValueError('Oversized fixed history')
    value = json.loads(path.read_text())
    bars = [RuleBar.model_validate(row) for row in value['bars']]
    normalized_hash=digest([row.model_dump(mode='json') for row in bars])
    if value['source']=='bitget.public':
        if normalized_hash!=value['dataset_sha256'] or value.get('synthetic') is not False:
            raise ValueError('Public crypto data hash/provenance mismatch')
    elif value['source']=='histdata.paired-ticks:1m':
        scope={key:value[key] for key in ('instrument','source','timeframe','bars')}
        scoped_hash=hashlib.sha256(json.dumps(scope,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if (value.get('version')!='gold-rule-history-1m-v1' or normalized_hash!=value['bars_sha256']
                or scoped_hash!=value['dataset_sha256']):
            raise ValueError('Gold minute data hash/provenance mismatch')
    else:
        raise ValueError('Unsupported fixed research history source')
    if any((row.instrument, row.source, row.timeframe) !=
           (value['instrument'], value['source'], value['timeframe']) for row in bars):
        raise ValueError('Historical scope mismatch')
    return value, bars


def fetch_history(directory, protocol):
    end = datetime.fromisoformat(protocol['end_exclusive'])
    public = BitgetPublic()
    try:
        server = public.server_time()
        if int(end.timestamp()*1000) > server:
            raise ValueError('Future study cutoff forbidden')
        for symbol in protocol['products']:
            for timeframe in protocol['timeframes']:
                target = directory/(symbol+'-'+timeframe+'.json')
                if target.exists():
                    load_history(target)  # Reuse frozen observations; never revise them.
                    continue
                step = INTERVALS[timeframe]
                start = end-timedelta(seconds=step*protocol['bars_per_scope'])
                raw = public.candles(symbol,timeframe,int(start.timestamp()*1000),int(end.timestamp()*1000),server)
                bars = [RuleBar(instrument=symbol,source=row.source,timeframe=timeframe,
                    open_time=row.open_time,close_time=row.close_time,open=row.open,high=row.high,
                    low=row.low,close=row.close,volume=row.volume,is_closed=row.is_closed).model_dump(mode='json') for row in raw]
                expected = [start+timedelta(seconds=step*i) for i in range(protocol['bars_per_scope'])]
                if [datetime.fromisoformat(row['open_time']) for row in bars] != expected:
                    raise ValueError('Incomplete public historical window')
                write_once(target, {'instrument':symbol,'source':'bitget.public','timeframe':timeframe,
                    'start':start.isoformat(),'end_exclusive':end.isoformat(),'bars':bars,
                    'dataset_sha256':digest(bars),'synthetic':False})
    finally:
        public.close()


def verify_result(path):
    original = json.loads(path.read_text())
    manifest = original['manifest']
    if manifest['news_sha256'] != digest([]):
        raise ValueError('This command requires the retained original historical news before replaying a covered run')
    replay = simulate(original['dataset'],manifest['config'],instrument=manifest['instrument'],
        source=manifest['source'],timeframe=manifest['timeframe'],as_of=datetime.fromisoformat(manifest['as_of']),
        news_policy=manifest['news_policy'])
    if replay != original:
        raise ValueError('Actual deterministic replay differs from saved full receipt')
    print(json.dumps({'verified_run':replay['run_id'],'full_receipt_equal':True,'trading_enabled':False}),flush=True)


def study(history, output, protocol, gold_history=None):
    source_hashes = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
    datasets, results = [], []
    scopes = []
    for symbol in protocol['products']:
        for timeframe in protocol['timeframes']:
            value,bars = load_history(history/(symbol+'-'+timeframe+'.json'))
            step=INTERVALS[timeframe]
            end=datetime.fromisoformat(protocol['end_exclusive'])
            start=end-timedelta(seconds=step*protocol['bars_per_scope'])
            if (value['instrument']!=symbol or value['source']!='bitget.public' or value['timeframe']!=timeframe
                    or len(bars)!=protocol['bars_per_scope'] or bars[0].open_time!=start or bars[-1].close_time!=end
                    or any(b.open_time-a.open_time!=timedelta(seconds=step) for a,b in zip(bars,bars[1:]))):
                raise ValueError('Crypto history does not match the frozen protocol')
            for window,(left,right) in protocol['windows'].items():
                scopes.append((value,bars[left:right],window,protocol['quantity'][symbol],protocol['base_costs'][symbol]))
    gold_status={'state':'UNAVAILABLE','detail':'未取得匹配完整性要求的真实 1m 黄金源外历史，未使用小时数据替代。'}
    gold_provenance=None
    if gold_history is not None:
        for window in protocol['gold_study']['windows']:
            value,bars=load_history(gold_history/protocol['gold_study']['files'][window])
            if (value['instrument']!='XAU/USD' or value['source']!=protocol['gold_study']['source']
                    or value['timeframe']!='1m' or len(bars)!=2000 or any(row.volume is not None for row in bars)):
                raise ValueError('Gold study must retain its independent real minute scope')
            scopes.append((value,bars,window,protocol['gold_study']['quantity'],protocol['gold_study']['base_costs']))
            evidence={key:value['provenance'][key] for key in ('page','zip_sha256','raw_csv_sha256','zip_bytes','raw_csv_bytes')}
            evidence.update({key:value['quality'][key] for key in ('ticks','complete_minutes','incomplete_minutes_excluded')})
            if gold_provenance is not None and gold_provenance!=evidence:
                raise ValueError('Gold windows do not share one retained raw monthly archive')
            gold_provenance=evidence
        gold_status={'state':'AVAILABLE','detail':'HistData 2025-01 真实配对 tick 聚合 1m、首尾窗口各 2000 样本；来源外诊断，不能视为 Swissquote 同源前向或真实盘口成交。'}
    for value,bars,window,quantity,costs in scopes:
        instrument,timeframe,source=value['instrument'],value['timeframe'],value['source']
        datasets.append({'instrument':instrument,'source':source,'timeframe':timeframe,'window':window,
            'start':bars[0].open_time.isoformat(),'end_exclusive':bars[-1].close_time.isoformat(),
            'bars':len(bars),'dataset_sha256':digest([row.model_dump(mode='json') for row in bars])})
        for rule in protocol['rules']:
            for scenario in ('BASE','STRESS'):
                selected={key:str(Decimal(price)*(Decimal(protocol['stress_multiplier'][key]) if scenario=='STRESS' else 1)) for key,price in costs.items()}
                config=PriceActionBacktestConfig(rule=rule,initial_cash=protocol['initial_cash'],quantity=quantity,
                    stop_loss_percent=protocol['stop_loss_percent'],take_profit_percent=protocol['take_profit_percent'],
                    tick_size=protocol['tick_size'],quantity_step=protocol['quantity_step'],max_quantity=protocol['max_quantity'],max_order_quote=protocol['max_order_quote'],
                    news_sources=(),news_max_age_seconds=protocol['news_max_age_seconds'],news_pre_seconds=protocol['news_pre_seconds'],news_post_seconds=protocol['news_post_seconds'],**selected)
                for policy in protocol['historical_news_policy']:
                    label={'instrument':instrument,'timeframe':timeframe,'window':window,'rule':rule,'scenario':scenario,'news_policy':policy}
                    print(json.dumps({'starting':label}),flush=True)
                    result=simulate(bars,config,instrument=instrument,source=source,timeframe=timeframe,
                        as_of=bars[-1].close_time,news_policy=policy)
                    if policy=='REQUIRE_HISTORICAL' and (result['metrics']['fill_count'] or Decimal(result['metrics']['total_return'])!=0):
                        raise ValueError('Missing historical news unexpectedly produced risk')
                    case_id=digest(label|{'protocol_sha256':digest(protocol),'run_id':result['run_id']})
                    write_once(output/'receipts'/(case_id+'.json'),result)
                    row={**label,'case_id':case_id,'run_id':result['run_id'],'metrics':result['metrics'],
                        'long_entries':sum(f['action']=='OPEN' and f['direction']=='LONG' for f in result['fills']),
                        'short_entries':sum(f['action']=='OPEN' and f['direction']=='SHORT' for f in result['fills']),
                        'stop_exits':sum(f['reason']=='STOP_LOSS' for f in result['fills']),
                        'profit_exits':sum(f['reason']=='TAKE_PROFIT' for f in result['fills']),
                        'costs':config.model_dump(mode='json'),'news_coverage':result['news_coverage']}
                    results.append(row)
                    print(json.dumps({'completed':label,'run_id':result['run_id'],'metrics':result['metrics']}),flush=True)
    if source_hashes!={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}:
        raise ValueError('Computation source changed during the study; keep receipts and rerun from the final code')
    strict=[row for row in results if row['news_policy']=='REQUIRE_HISTORICAL']
    identity=digest({'protocol':protocol,'datasets':datasets,'results':results,'source_sha256':source_hashes})
    summary={'version':protocol['version'],'study_id':identity,'protocol_sha256':digest(protocol),
        'generated_at':datetime.now(timezone.utc).isoformat(),'source_sha256':source_hashes,
        'datasets':datasets,'results':results,'gold_status':gold_status,'gold_provenance':gold_provenance,
        'strict_gate':{'cases':len(strict),'fills':sum(row['metrics']['fill_count'] for row in strict),'verified':True},
        'trading_enabled':False,'live_eligible':False,'historical_news_verified':False,
        'limits':['未取得真实历史新闻：严格模式禁止开仓；未覆盖消息的诊断收益不能获得线上交易许可。',
            'OHLC 叠加假设完整点差与单边不利滑点是模型成交，不是历史实际 bid/ask、盘口或逐 tick 成交。',
            '后 40% 为可见且可重复的时间分段，不是密封盲测；每项独立资金和预热。',
            '黄金 HistData 与实时 Swissquote 来源和采样密度不同，不能证明同源前向表现。',
            '回撤按每柱收盘权益计算；期末不强平，未平仓标记尚未扣未来平仓费用和滑点。',
            '合成做空不代表现货可卖空；不含杠杆、资金费、借贷或历史深度容量模型。']}
    if (output/'study.json').exists():
        previous=json.loads((output/'study.json').read_text())
        if previous['study_id']!=identity:
            raise ValueError('Existing study belongs to other fixed inputs/code; choose a new result directory')
        summary['generated_at']=previous['generated_at']
    write_once(output/'study.json',summary)
    write_once(output/'protocol.json',protocol)
    print(json.dumps({'study_id':identity,'cases':len(results),'strict_fills':summary['strict_gate']['fills'],'gold':gold_status['state']}),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--history',type=Path)
    parser.add_argument('--gold-history',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--fetch',action='store_true')
    parser.add_argument('--verify-result',type=Path)
    args=parser.parse_args()
    if args.verify_result:
        verify_result(args.verify_result);return
    if args.history is None or args.output is None:
        parser.error('--history and --output required for a study')
    protocol=json.loads(PROTOCOL.read_text())
    if args.fetch:
        args.history.mkdir(parents=True,exist_ok=True);fetch_history(args.history,protocol)
    study(args.history,args.output,protocol,args.gold_history)


if __name__=='__main__':
    main()
