"""Freeze one public HistData month into source-labelled, complete 1m mid bars.

This is not Swissquote history or historical executable bid/ask replay. No news,
orders, account storage, fees or model inference are supplied by this collector.
"""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
from time import monotonic
from typing import Iterable
import zipfile

import httpx

from scripts.import_gold_history import download
from core.backtest.spot import digest
from core.paper.price_action_rules import RuleBar

YEAR, MONTH = 2025, 1
VERSION = 'gold-rule-history-1m-v1'
SOURCE = 'histdata.paired-ticks:1m'
SHA_SCOPE = {'dataset_sha256': 'SHA256(canonical({instrument,source,timeframe,bars})); original serialized bars',
             'bars_sha256': 'core.backtest.spot.digest([RuleBar.model_validate(row).model_dump(mode="json") for row in bars])',
             'artifact_sha256': 'SHA256(exact JSON file bytes); recorded in collection-manifest.json'}
MINUTE = 60000
ZIP_LIMIT = 300000000
CSV_LIMIT = 3000000000
EST = timezone(timedelta(hours=-5))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def file_sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def dataset_sha(value):
    """Content identity excludes output paths and variable collection timings."""
    return hashlib.sha256(canonical({key: value[key] for key in ('instrument', 'source', 'timeframe', 'bars')})).hexdigest()


def normalized_bars_sha(bars):
    return digest([RuleBar.model_validate(row).model_dump(mode='json') for row in bars])


def parse_ticks(lines: Iterable[bytes]):
    """Stream original ASCII lines; keep only bounded per-minute accumulators."""
    groups, quarantined = {}, set()
    quality = {'ticks': 0, 'identical_duplicates': 0, 'conflicting_timestamp_events': 0,
               'backwards_tick_events': 0, 'quarantined_minutes': 0,
               'incomplete_minutes_excluded': 0, 'complete_minutes': 0}
    raw_hash, raw_bytes = hashlib.sha256(), 0
    high_water, high_pair = None, None
    spread_min = spread_max = None
    day_cache = None
    for raw in lines:
        raw_bytes += len(raw)
        if raw_bytes > CSV_LIMIT:
            raise ValueError('Expanded gold tick data exceeds 3 GB')
        raw_hash.update(raw)
        fields = raw.decode('ascii').strip().split(',')
        if len(fields) != 4 or not re.fullmatch(r'\d{8} \d{9}', fields[0]):
            raise ValueError('Exact HistData DateTime,Bid,Ask,Volume fields required')
        stamp, bid_text, ask_text, _ = fields
        day = stamp[:8]
        if day_cache is None or day_cache[0] != day:
            date = datetime.strptime(day, '%Y%m%d').replace(tzinfo=EST)
            if (date.year, date.month) != (YEAR, MONTH):
                raise ValueError('Only the fixed completed 2025-01 source month is accepted')
            day_cache = day, int(date.timestamp()) * 1000
        hour, minute, second, millisecond = map(int, (stamp[9:11], stamp[11:13], stamp[13:15], stamp[15:18]))
        if hour > 23 or minute > 59 or second > 59:
            raise ValueError('Invalid HistData clock')
        moment = day_cache[1] + hour * 3600000 + minute * MINUTE + second * 1000 + millisecond
        bid, ask = Decimal(bid_text), Decimal(ask_text)
        if not bid.is_finite() or not ask.is_finite() or not 0 < bid <= ask:
            raise ValueError('Finite positive uncrossed paired quote required')
        if any(len(value.as_tuple().digits) > 38 or value.as_tuple().exponent < -18 for value in (bid, ask)):
            raise ValueError('Gold quote precision exceeds RuleBar bounds')
        quality['ticks'] += 1
        with localcontext() as precision:
            precision.prec = 60
            spread, mid = ask - bid, (bid + ask) / 2
        spread_min = spread if spread_min is None else min(spread_min, spread)
        spread_max = spread if spread_max is None else max(spread_max, spread)
        start = moment // MINUTE * MINUTE
        if high_water is not None and moment < high_water:
            if high_water - moment > 86400000:
                raise ValueError('Tick clock reversed by more than one day; archive rejected')
            quality['backwards_tick_events'] += 1
            quarantined.update(range(start, high_water // MINUTE * MINUTE + MINUTE, MINUTE))
            continue
        if high_water == moment:
            if high_pair == (bid, ask):
                quality['identical_duplicates'] += 1
            else:
                quality['conflicting_timestamp_events'] += 1
                quarantined.add(start)
            continue
        high_water, high_pair = moment, (bid, ask)
        if start in quarantined:
            continue
        current = groups.get(start)
        if current is None:
            groups[start] = {'first': moment, 'last': moment, 'open': mid,
                             'high': mid, 'low': mid, 'close': mid, 'samples': 1, 'max_gap': 0}
        else:
            current['max_gap'] = max(current['max_gap'], moment - current['last'])
            current.update(last=moment, close=mid, high=max(current['high'], mid),
                           low=min(current['low'], mid), samples=current['samples'] + 1)
    bars = []
    for start, row in sorted(groups.items()):
        if start in quarantined:
            continue
        if row['first'] - start > 20000 or start + MINUTE - row['last'] > 20000 or row['max_gap'] > 30000:
            quality['incomplete_minutes_excluded'] += 1
            continue
        value = {'instrument': 'XAU/USD', 'source': SOURCE, 'timeframe': '1m',
                     'open_time': (EPOCH + timedelta(milliseconds=start)).isoformat(),
                     'close_time': (EPOCH + timedelta(milliseconds=start + MINUTE)).isoformat(),
                     **{key: str(row[key]) for key in ('open', 'high', 'low', 'close')},
                     'volume': None, 'is_closed': True}
        RuleBar.model_validate(value)
        bars.append(value)
    quality.update(quarantined_minutes=len(quarantined), complete_minutes=len(bars),
                   raw_csv_bytes=raw_bytes, raw_csv_sha256=raw_hash.hexdigest(),
                   observed_spread_min=None if spread_min is None else str(spread_min),
                   observed_spread_max=None if spread_max is None else str(spread_max))
    return bars, quality


def decode_archive(path):
    path = Path(path)
    if path.stat().st_size > ZIP_LIMIT:
        raise ValueError('Monthly archive exceeds 300 MB')
    expected = f'DAT_ASCII_XAUUSD_T_{YEAR}{MONTH:02}.csv'
    with zipfile.ZipFile(path) as archive:
        if archive.namelist().count(expected) != 1 or archive.getinfo(expected).file_size > CSV_LIMIT:
            raise ValueError('Unexpected raw paired-tick member or expanded archive size')
        with archive.open(expected) as stream:
            bars, quality = parse_ticks(stream)
    return bars, quality


def freeze(output):
    output = Path(output)
    archives = output / 'archives'
    archives.mkdir(parents=True, exist_ok=True)
    started = monotonic()
    # One ordinary public form flow. No retries, credentials, paid feeds or bypass.
    with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
        path, page = download(client, YEAR, MONTH, archives)
    download_seconds = monotonic() - started
    bars, quality = decode_archive(path)
    provenance = {'source_calendar_month': '2025-01', 'timezone': 'fixed EST UTC-05:00; no DST',
                  'page': page, 'zip_path': str(path), 'zip_bytes': path.stat().st_size,
                  'zip_sha256': file_sha(path), 'download_seconds': download_seconds,
                  'raw_csv_sha256': quality['raw_csv_sha256'], 'raw_csv_bytes': quality['raw_csv_bytes']}
    limitations = ['HistData is a different source from Swissquote; no source transfer qualification.',
                  'Raw paired bid/ask is provenance for mid OHLC only; backtest spread remains an explicit assumption.',
                  'All source ticks differ from the live 10-second quote sample density.',
                  'No historical news calendar is supplied; no news-validated or live eligibility claim.',
                  'Missing/quarantined minutes remain gaps; no interpolation or invented volume.']
    manifest = {'version': VERSION, 'sha_scope': SHA_SCOPE, 'instrument': 'XAU/USD', 'source': SOURCE, 'timeframe': '1m', 'quality': quality,
                'provenance': provenance, 'limitations': limitations, 'windows': []}
    if len(bars) < 4000:
        manifest['error'] = 'Fewer than 4000 complete minutes; disjoint early/late 2000 windows unavailable'
        (output / 'collection-manifest.json').write_bytes(canonical(manifest))
        raise ValueError(manifest['error'])
    for label, selected in (('early', bars[:2000]), ('late', bars[-2000:])):
        value = {'instrument': 'XAU/USD', 'source': SOURCE, 'timeframe': '1m', 'bars': selected,
                 'quality': quality, 'provenance': provenance, 'limitations': limitations,
                 'selection': f'Chronological {label} 2000 complete minutes; no result-based selection'}
        value['dataset_sha256'] = dataset_sha(value)
        value.update(version=VERSION, sha_scope=SHA_SCOPE, bars_sha256=normalized_bars_sha(selected))
        destination = output / f'gold-2025-01-{label}-2000.json'
        destination.write_bytes(canonical(value))
        manifest['windows'].append({'label': label, 'path': str(destination), 'bytes': destination.stat().st_size,
            'dataset_sha256': value['dataset_sha256'], 'bars_sha256': value['bars_sha256'],
            'artifact_sha256': file_sha(destination), 'bars': len(selected),
            'from': selected[0]['open_time'], 'until': selected[-1]['close_time']})
    (output / 'collection-manifest.json').write_bytes(canonical(manifest))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[3] / 'outputs/price-action-paper-20261115/gold-1m')
    args = parser.parse_args()
    print(json.dumps(freeze(args.output), sort_keys=True), flush=True)
