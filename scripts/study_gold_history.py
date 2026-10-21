"""Reproduce every fixed gold strategy/cost/year case from an immutable snapshot."""
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time

from core.gold.backtest import compare
from core.gold.history import Catalogue, canonical
from core.strategy.registry import DEFINITIONS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--commission', required=True, help='Assumed single-side USD/oz')
    parser.add_argument('--slippage', required=True, help='Assumed adverse single-side USD/oz')
    args = parser.parse_args()
    data, bars = Catalogue(args.history).load(args.dataset)
    request = {'dataset_id': args.dataset, 'start': data['start'], 'end': data['end'],
               'timeframes': ['1h', '4h'],
               'variants': [{'strategy': name, 'parameters': definition.create({})[1].model_dump(mode='json')}
                            for name, definition in DEFINITIONS.items()],
               'costs': {'initial_cash': '10000', 'allocation': '.5', 'commission': args.commission,
                         'slippage': args.slippage, 'spread_multiplier': '1'}}
    started = time.monotonic()
    result = compare(data, bars, request)
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'study.json').write_bytes(canonical(result))
    (directory/'manifest.json').write_text(json.dumps(result['manifest'], indent=2), encoding='utf-8')
    fields = ['strategy', 'timeframe', 'window', 'role', 'scenario', 'effective_start', 'effective_end',
              'total_return', 'max_drawdown', 'net_pnl', 'fees', 'spread_cost', 'slippage_cost',
              'closed_trades', 'open_ounces', 'return_difference_vs_buy_hold']
    with (directory/'comparison.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in result['results']:
            writer.writerow({key: row.get(key, row['metrics'].get(key)) for key in fields})
    day = lambda value: datetime.fromtimestamp(value/1000, timezone.utc).date().isoformat()
    lines = ['# XAU/USD 固定策略历史验证', '',
             f"请求窗口：{day(data['start'])} 至 {day(data['end'])}（结束日不含）。",
             f"数据：{args.dataset}；真实tick {data['quality']['ticks']:,}；完整1h柱 {len(bars):,}。",
             f"成本假设：单边手续费 {args.commission} USD/oz，单边不利滑点 {args.slippage} USD/oz，原始真实点差1倍。",
             '', '全部策略、年份及成本场景见 comparison.csv；完整收据见 study.json，源ZIP与代码摘要见 manifest.json。',
             '每项独立10000美元、投入50%、仅做多/空仓；持仓期末不强平。', '',
             '|周期|区间|策略|成本场景|净收益|收盘最大回撤|相对持有百分点|闭合交易|',
             '|---|---|---|---|---:|---:|---:|---:|']
    for row in result['results']:
        m = row['metrics']
        percent = lambda key: f"{Decimal(m[key])*100:.2f}"
        lines.append(f"|{row['timeframe']}|{row['window']} ({row['role']})|{row['strategy']}|{row['scenario']}|{percent('total_return')}%|{percent('max_drawdown')}%|{percent('return_difference_vs_buy_hold')}|{m['closed_trades']}|")
    lines += ['', '限制：历史含休市与未知缺口，仅代表完整观测子集；指标沿观测柱延续，下一周期缺首报价不追补成交。',
              '时间保留期可见且可重复研究，不是密封盲测。回撤为收盘权益口径。未计隔夜掉期、融资、真实深度。',
              '费用及额外滑点是假设，未获历史经纪商成交单证；HistData不是实时Swissquote源。',
              '不据此证明未来盈利，不连接AI或真实经纪商；实盘关闭。']
    (directory/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'run_id': result['run_id'], 'cases': len(result['results']),
                      'seconds': round(time.monotonic()-started, 2), 'result_bytes': len(canonical(result)),
                      'trading_enabled': False}), flush=True)


if __name__ == '__main__':
    main()
