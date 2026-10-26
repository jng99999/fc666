"""Offline mechanics validation on verified paired gold history, never live trading.

The default has no news provider and must produce zero entries. The separately
labelled diagnostic substitutes a synthetic clear calendar to exercise fills;
its returns are not a historical news-filtered strategy result.
"""
import argparse
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path

from core.gold.history import Catalogue, HOUR, canonical
from core.paper.directional import Config, DirectionalPaper, News, Proposal, Quote


def study(data, bars, dataset_id, *, fee_rate, slippage, stop_ratio, profit_ratio, quantity):
    if not Decimal(0) < stop_ratio < 1 or not Decimal(0) < profit_ratio < 1:
        raise ValueError('Protective ratios must lie strictly between zero and one')
    years = defaultdict(list)
    for bar in bars:
        years[datetime.fromtimestamp(bar.start / 1000, timezone.utc).year].append(bar)
    results = []
    for year, group in sorted(years.items()):
        for diagnostic in (False, True):
            source = 'SYNTHETIC_DIAGNOSTIC_CLEAR_CALENDAR_NOT_NEWS'
            config = Config(mode='RESEARCH_PAPER', instrument='XAU/USD', quote_source='HistData.paired.complete-hour-close',
                            news_sources=(source,), initial_cash='10000', tick_size='.01', quantity_step='.01',
                            max_quantity='10', max_order_quote='9000', fee_rate=fee_rate, slippage=slippage,
                            quote_max_age_seconds=300, decision_ttl_seconds=3600, news_max_age_seconds=1,
                            news_pre_seconds=1800, news_post_seconds=1800)
            engine = DirectionalPaper(config)
            history = deque(maxlen=20)
            peak = config.initial_cash
            drawdown = Decimal(0)
            for index, bar in enumerate(group):
                now = datetime.fromtimestamp((bar.start + HOUR) / 1000, timezone.utc)
                q = Quote(instrument=config.instrument, source=config.quote_source,
                          at=datetime.fromtimestamp(bar.last / 1000, timezone.utc), bid=bar.bid_close, ask=bar.ask_close)
                snapshot = News(source=source, observed_at=now, coverage_start=now - timedelta(days=1),
                                coverage_end=now + timedelta(days=1), events=()) if diagnostic else None
                view = engine.on_quote(q, now=now, news=snapshot)
                equity = Decimal(view['equity'])
                peak = max(peak, equity)
                drawdown = max(drawdown, (peak - equity) / peak)
                history.append(bar.close)
                if len(history) < 20 or engine.pending is not None:
                    continue
                mean = sum(history, Decimal(0)) / 20
                if bar.close == mean:
                    continue
                target = 'LONG' if bar.close > mean else 'SHORT'
                if engine.position is not None:
                    if engine.position['direction'] == target:
                        continue
                    decision = Proposal(request_id=f'{year}-{index}-FLAT', target='FLAT', available_at=now, quantity='0')
                else:
                    stop = bar.close * (1 - stop_ratio if target == 'LONG' else 1 + stop_ratio)
                    profit = bar.close * (1 + profit_ratio if target == 'LONG' else 1 - profit_ratio)
                    decision = Proposal(request_id=f'{year}-{index}-{target}', target=target, available_at=now,
                                        quantity=quantity, stop_loss=stop, take_profit=profit)
                engine.submit(decision, now=now, news=snapshot)
            final = engine.view()
            opens = [fill for fill in final['fills'] if fill['action'] == 'OPEN']
            if not diagnostic and opens:
                raise ArithmeticError('No provider must mean no new risk')
            results.append({'year': year, 'scenario': 'DIAGNOSTIC_NO_REAL_NEWS' if diagnostic else 'NEWS_REQUIRED_UNAVAILABLE',
                            'bars': len(group), 'first': group[0].start, 'last': group[-1].start + HOUR,
                            'long_entries': sum(fill['direction'] == 'LONG' for fill in opens),
                            'short_entries': sum(fill['direction'] == 'SHORT' for fill in opens),
                            'stop_exits': sum(fill['reason'] == 'STOP_LOSS' for fill in final['fills']),
                            'profit_exits': sum(fill['reason'] == 'TAKE_PROFIT' for fill in final['fills']),
                            'observed_quote_max_drawdown': str(drawdown),
                            'return': str((Decimal(final['equity']) - config.initial_cash) / config.initial_cash),
                            'news_validated': False, 'config': config.model_dump(mode='json'), 'final': final})
    paths = ['core/paper/directional.py', 'core/gold/history.py', 'scripts/study_directional_gold.py']
    root = Path(__file__).resolve().parents[1]
    return {'trading_enabled': False, 'dataset_id': dataset_id, 'history_provenance': {k: v for k, v in data.items() if k not in ('bars', 'opens')},
            'code_sha256': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths},
            'parameters': {'stop_ratio': str(stop_ratio), 'profit_ratio': str(profit_ratio), 'quantity': str(quantity)},
            'assumptions': ['Independent 10000 USD per calendar year and scenario; SMA20 resets/warmup each year',
                            'Synthetic fully collateralized directional contract; not a broker/spot/perpetual specification',
                            'Only actual paired closing quotes from complete hours; gaps remain visible',
                            'Next observed quote after decision acceptance; not next-bar-open execution',
                            'Protection checked only on sampled closes; intrahour stops cannot be reconstructed',
                            'Actual historical bid/ask plus explicit fee-rate and absolute adverse slippage assumptions',
                            'No financing/funding/overnight swaps, executable depth or forced terminal liquidation',
                            'No historical news source: strict mode abstains; diagnostic is mechanics-only, not news qualification'],
            'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output', required=True)
    for field in ('fee-rate', 'slippage', 'stop-ratio', 'profit-ratio', 'quantity'):
        parser.add_argument('--' + field, required=True)
    args = parser.parse_args()
    data, bars = Catalogue(args.history).load(args.dataset)
    with localcontext() as precision:
        precision.prec = 60
        result = study(data, bars, args.dataset, fee_rate=args.fee_rate, slippage=args.slippage,
                       stop_ratio=Decimal(args.stop_ratio), profit_ratio=Decimal(args.profit_ratio), quantity=args.quantity)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'study.json').write_bytes(canonical(result))
    fields = ['year', 'scenario', 'bars', 'long_entries', 'short_entries', 'stop_exits', 'profit_exits', 'return', 'news_validated']
    summary = [{**{key: row[key] for key in fields}, 'fees': row['final']['fees']} for row in result['results']]
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({'cases': len(summary), 'trading_enabled': False, 'summary': summary}), flush=True)


if __name__ == '__main__':
    main()
