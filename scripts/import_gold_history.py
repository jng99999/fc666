"""Download HistData's public monthly tick exports; archive provenance, never synthesize gaps."""
import argparse
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import time
import zipfile

import httpx

from core.gold.history import Bar, HOUR, VERSION, canonical, sha


def download(client, year, month, cache):
    path = cache / f'XAUUSD-T-{year}{month:02}.zip'
    page = f'https://www.histdata.com/download-free-forex-historical-data/?/ascii/tick-data-quotes/xauusd/{year}/{month}'
    if not path.exists():
        response = client.get(page)
        response.raise_for_status()
        form = re.search(r'<form id="file_down".*?</form>', response.text, re.S)
        if form is None:
            raise ValueError(f'No public monthly download form for {year}-{month:02}')
        fields = dict(re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', form.group()))
        if fields.get('fxpair') != 'XAUUSD' or fields.get('timeframe') != 'T':
            raise ValueError('Unexpected HistData instrument/timeframe')
        temporary = path.with_suffix('.partial')
        try:
            with client.stream('POST', 'https://www.histdata.com/get.php', data=fields, headers={'Referer': page}) as result:
                result.raise_for_status()
                size = 0
                with temporary.open('wb') as output:
                    for chunk in result.iter_bytes():
                        size += len(chunk)
                        if size > 300000000:
                            raise ValueError('Monthly gold archive exceeds 300 MB')
                        output.write(chunk)
            with zipfile.ZipFile(temporary) as archive:
                if not any(n.endswith('.csv') for n in archive.namelist()):
                    raise ValueError('Historical download is not a tick CSV archive')
            temporary.rename(path)
        finally:
            if temporary.exists():
                temporary.unlink()
        time.sleep(1)
    return path, page


def parse_archive(path, year, month):
    bars, current = [], None
    count = duplicates = omitted = backwards = 0
    previous = None
    base = None
    quarantined = set()

    def finish():
        nonlocal omitted
        if current is None:
            return
        if current[0] in quarantined:
            return
        try:
            bars.append(Bar.parse(current))
        except ValueError:
            omitted += 1

    with zipfile.ZipFile(path) as archive:
        expected = f'DAT_ASCII_XAUUSD_T_{year}{month:02}.csv'
        if archive.namelist().count(expected) != 1 or archive.getinfo(expected).file_size > 3000000000:
            raise ValueError('Unexpected tick archive member or expanded size')
        with archive.open(expected) as stream:
            for raw in stream:
                stamp, bid, ask, volume = raw.decode('ascii').strip().split(',')
                if len(stamp) != 18 or stamp[8] != ' ' or not (stamp[:8]+stamp[9:]).isdigit():
                    raise ValueError('Unexpected HistData timestamp format')
                day = stamp[:8]
                if base is None or base[0] != day:
                    date = datetime.strptime(day, '%Y%m%d').replace(tzinfo=timezone(timedelta(hours=-5)))
                    if date.year != year or date.month != month:
                        raise ValueError('Tick outside requested source month')
                    base = (day, int(date.timestamp())*1000)
                moment = base[1]+int(stamp[9:11])*HOUR+int(stamp[11:13])*60000+int(stamp[13:15])*1000+int(stamp[15:18])
                if int(stamp[9:11]) > 23 or int(stamp[11:13]) > 59 or int(stamp[13:15]) > 59:
                    raise ValueError('Invalid source clock time')
                bid, ask = Decimal(bid), Decimal(ask)
                if not bid.is_finite() or not ask.is_finite() or not 0 < bid <= ask:
                    raise ValueError('Invalid source tick bid/ask')
                if previous and moment < previous[0]:
                    if previous[0]-moment > 86400000:
                        raise ValueError('Historical ticks reversed by more than a day')
                    backwards += 1
                    quarantined.update(range(moment//HOUR*HOUR, previous[0]//HOUR*HOUR+HOUR, HOUR))
                    bars = [bar for bar in bars if bar.start not in quarantined]
                count += 1
                if previous == (moment, bid, ask):
                    duplicates += 1
                    continue
                mid = (bid+ask)/2
                start = moment//HOUR*HOUR
                if current is None or start != current[0]:
                    finish()
                    current = [start, moment, moment, str(bid), str(ask), str(bid), str(ask), str(mid), str(mid), 1, 0]
                else:
                    current[10] = max(current[10], moment-current[2])
                    current[2] = moment
                    current[5:9] = [str(bid), str(ask), str(max(Decimal(current[7]), mid)), str(min(Decimal(current[8]), mid))]
                    current[9] += 1
                previous = (moment, bid, ask)
    finish()
    return bars, {'ticks': count, 'identical_duplicates': duplicates, 'incomplete_hours_excluded': omitted,
                  'backwards_tick_events': backwards, 'ambiguous_hours_excluded': len(quarantined)}


def opening_quotes(path, year, month):
    """Keep first quotes even when that hour later has incomplete candle coverage.

    Called only after the entire archive passed the full decoder validation.
    Clock-ambiguous source hours remain quarantined; no timestamp repair.
    """
    quotes, quarantined = {}, set()
    previous_stamp, previous_start = None, None
    hour_key, start = None, None
    with zipfile.ZipFile(path) as archive:
        with archive.open(f'DAT_ASCII_XAUUSD_T_{year}{month:02}.csv') as stream:
            for raw in stream:
                stamp = raw[:18]
                if stamp[:11] != hour_key:
                    hour_key = stamp[:11]
                    date = datetime.strptime(hour_key.decode('ascii'), '%Y%m%d %H').replace(tzinfo=timezone(timedelta(hours=-5)))
                    start = int(date.timestamp()*1000)
                if previous_stamp is not None and stamp < previous_stamp:
                    quarantined.update(range(start, previous_start+HOUR, HOUR))
                if start not in quotes:
                    fields = raw.decode('ascii').strip().split(',')
                    moment = start+int(stamp[11:13])*60000+int(stamp[13:15])*1000+int(stamp[15:18])
                    quotes[start] = [start, moment, str(Decimal(fields[1])), str(Decimal(fields[2]))]
                previous_stamp, previous_start = stamp, start
    return [quote for hour, quote in sorted(quotes.items()) if hour not in quarantined]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True, help='YYYY-MM first complete month')
    parser.add_argument('--end', required=True, help='YYYY-MM exclusive month')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    start = datetime.strptime(args.start, '%Y-%m').replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, '%Y-%m').replace(tzinfo=timezone.utc)
    if not start < end <= datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0):
        raise ValueError('Require completed months in chronological order')
    directory = Path(args.output)
    cache = directory / 'archives'
    cache.mkdir(parents=True, exist_ok=True)
    bars, opens, sources, quality = [], [], [], {'ticks': 0, 'identical_duplicates': 0, 'incomplete_hours_excluded': 0,
                                    'backwards_tick_events': 0, 'ambiguous_hours_excluded': 0}
    current = start
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        while current < end:
            path, page = download(client, current.year, current.month, cache)
            archive_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            decoded = path.with_suffix('.decoded.json')
            cached = json.loads(decoded.read_text()) if decoded.exists() else None
            if cached and cached.get('archive_sha256') == archive_hash and cached.get('decoder') == 'quarantine-clock-v1':
                rows, stats = [Bar.parse(row) for row in cached['bars']], cached['quality']
            else:
                rows, stats = parse_archive(path, current.year, current.month)
                decoded.write_bytes(canonical({'archive_sha256': archive_hash, 'decoder': 'quarantine-clock-v1',
                                               'bars': [bar.row() for bar in rows], 'quality': stats}))
            bars.extend(rows)
            opening_cache = path.with_suffix('.openings.json')
            cached_opens = json.loads(opening_cache.read_text()) if opening_cache.exists() else None
            if cached_opens and cached_opens.get('archive_sha256') == archive_hash:
                opens.extend(cached_opens['opens'])
            else:
                monthly_opens = opening_quotes(path, current.year, current.month)
                opening_cache.write_bytes(canonical({'archive_sha256': archive_hash, 'opens': monthly_opens}))
                opens.extend(monthly_opens)
            for key, value in stats.items():
                quality[key] += value
            sources.append({'month': current.strftime('%Y-%m'), 'url': page, 'sha256': archive_hash, 'bytes': path.stat().st_size, **stats})
            print(json.dumps(sources[-1]), flush=True)
            current = current.replace(year=current.year+1, month=1) if current.month == 12 else current.replace(month=current.month+1)
    # The source's fixed EST calendar crosses UTC month bounds by five hours.
    first, last = int(start.timestamp()*1000), int(end.timestamp()*1000)
    bars = [bar for bar in bars if first <= bar.start and bar.start+HOUR <= last]
    opens = [quote for quote in opens if first <= quote[0] < last]
    if any(b.start <= a.start for a, b in zip(bars, bars[1:])):
        raise ValueError('Monthly history overlap/conflict')
    quality['unobserved_utc_hours_including_closures'] = (last-first)//HOUR-len(bars)
    data = {'version': VERSION, 'source': 'HistData.com ASCII paired XAUUSD ticks; fixed EST UTC-05:00',
            'symbol': 'XAU/USD', 'start': first, 'end': last, 'quality': quality, 'sources': sources,
            'bars': [bar.row() for bar in bars], 'opens': opens}
    identity = sha(data)
    destination = directory / (identity+'.json.gz')
    with gzip.GzipFile(filename=str(destination), mode='wb', mtime=0) as output:
        output.write(canonical(data))
    print(json.dumps({'dataset': identity, 'bars': len(bars), 'path': str(destination), 'quality': quality}), flush=True)


if __name__ == '__main__':
    main()
