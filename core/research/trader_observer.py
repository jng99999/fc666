"""Bounded public-account observation. No keys, orders, copy trading, or polling."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

import httpx


INFO_URL = 'https://api.hyperliquid.xyz/info'
LEADERBOARD_URL = 'https://stats-data.hyperliquid.xyz/Mainnet/leaderboard'
DOCS_URL = 'https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint'
ADDRESS = re.compile(r'^0x[0-9a-fA-F]{40}$')
PAGE_LIMIT = 2000
RETENTION_LIMIT = 10000


class ObservationError(ValueError):
    """Stable public-data failure; never retries an uncertain request implicitly."""


def utc(stamp):
    return datetime.fromtimestamp(stamp/1000, timezone.utc).isoformat()


def decimal_text(value, *, positive=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 96:
        raise ObservationError('INVALID_DECIMAL')
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ObservationError('INVALID_DECIMAL') from error
    if (not number.is_finite() or abs(number) > Decimal('1e30')
            or abs(number.as_tuple().exponent) > 64 or (positive and number <= 0)):
        raise ObservationError('INVALID_DECIMAL')
    return number


def normalize_fill(row, trader, start, end, now):
    if not isinstance(row, dict):
        raise ObservationError('INVALID_FILL')
    try:
        stamp, tid, oid = row['time'], row['tid'], row['oid']
        if any(type(value) is not int or value < 0 for value in (stamp, tid, oid)):
            raise ObservationError('INVALID_FILL_ID_OR_TIME')
        if stamp > now or not start <= stamp <= end:
            raise ObservationError('FUTURE_OR_OUT_OF_RANGE_FILL')
        coin, side, direction = row['coin'], row['side'], row['dir']
        if (not isinstance(coin, str) or not 1 <= len(coin) <= 64 or side not in ('A', 'B')
                or not isinstance(direction, str) or not 1 <= len(direction) <= 96):
            raise ObservationError('INVALID_FILL_IDENTITY')
        price = decimal_text(row['px'], positive=True)
        size = decimal_text(row['sz'], positive=True)
        before = decimal_text(row['startPosition'])
        pnl, fee = decimal_text(row['closedPnl']), decimal_text(row['fee'])
        fee_token, transaction = row['feeToken'], row['hash']
        if (not isinstance(fee_token, str) or not 1 <= len(fee_token) <= 32
                or not isinstance(transaction, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', transaction)):
            raise ObservationError('INVALID_FILL_PROVENANCE')
        builder_fee = decimal_text(row['builderFee']) if 'builderFee' in row else None
    except KeyError as error:
        raise ObservationError('MISSING_FILL_FIELD') from error
    with localcontext() as precision:
        precision.prec = 200
        after = before + (size if side == 'B' else -size)
    confirmed = {
        'Open Long': side == 'B' and before >= 0,
        'Close Long': side == 'A' and before > 0 and after >= 0,
        'Open Short': side == 'A' and before <= 0,
        'Close Short': side == 'B' and before < 0 and after <= 0,
        'Long > Short': side == 'A' and before > 0 and after < 0,
        'Short > Long': side == 'B' and before < 0 and after > 0,
    }
    if direction in confirmed and not confirmed[direction]:
        raise ObservationError('INCONSISTENT_POSITION_DIRECTION')
    return {'event_id': f'{trader}:{coin}:{tid}', 'trader': trader, 'coin': coin,
            'time_ms': stamp, 'time_utc': utc(stamp), 'trade_id': str(tid), 'order_id': str(oid),
            'transaction_hash': transaction, 'side': side, 'reported_direction': direction,
            'direction_status': 'POSITION_CHECKED' if direction in confirmed else 'UNCLASSIFIED',
            'price': str(price), 'size': str(size), 'reported_start_position': str(before),
            'derived_end_position': str(after) if direction in confirmed else None,
            'reported_closed_pnl': str(pnl), 'fee': str(fee), 'fee_token': fee_token,
            'reported_builder_fee': str(builder_fee) if builder_fee is not None else None}


def quantiles(values):
    ordered = sorted(values)
    if not ordered:
        return None
    return {'method': 'nearest_rank', 'min': str(ordered[0]),
            'p25': str(ordered[(len(ordered)+3)//4-1]),
            'p50': str(ordered[(2*len(ordered)+3)//4-1]),
            'p75': str(ordered[(3*len(ordered)+3)//4-1]), 'max': str(ordered[-1])}


def behavior_features(fills, pagination_truncated):
    """Describe only continuous observed zero-to-zero episodes, never infer missing inventory."""
    grouped = defaultdict(list)
    for fill in fills:
        grouped[fill['coin']].append(fill)
    results = {}
    for coin, rows in sorted(grouped.items()):
        rows.sort(key=lambda fill: (fill['time_ms'], int(fill['trade_id'])))
        episodes, current, reasons = [], [], Counter()
        previous = None
        breaks = 0
        for fill in rows:
            before = Decimal(fill['reported_start_position'])
            after = Decimal(fill['derived_end_position']) if fill['derived_end_position'] is not None else None
            if after is None:
                reasons['UNCLASSIFIED_DIRECTION'] += 1
                if current:
                    reasons['CHAIN_INTERRUPTED_BY_UNCLASSIFIED_DIRECTION'] += 1
                current, previous = [], None
                continue
            if current and before != previous:
                breaks += 1
                reasons['POSITION_DISCONTINUITY'] += 1
                current, previous = [], None
            if before == 0 and after != 0:
                current, previous = [fill], after
                continue
            if not current:
                reasons['NO_OBSERVED_FLAT_START'] += 1
                continue
            opening_sign = Decimal(current[0]['derived_end_position'])
            if after*opening_sign < 0:
                reasons['CROSS_ZERO_WITHOUT_OBSERVED_FLAT'] += 1
                current, previous = [], None
                continue
            current.append(fill)
            previous = after
            if after == 0:
                increases = sum(Decimal(item['derived_end_position']).copy_abs() > Decimal(item['reported_start_position']).copy_abs() for item in current)
                partial_reductions = sum(0 < Decimal(item['derived_end_position']).copy_abs() < Decimal(item['reported_start_position']).copy_abs() for item in current)
                with localcontext() as precision:
                    precision.prec = 200
                    duration = Decimal(fill['time_ms']-current[0]['time_ms'])/1000
                episodes.append({'side': 'LONG' if opening_sign > 0 else 'SHORT',
                                 'start_time_ms': current[0]['time_ms'], 'end_time_ms': fill['time_ms'],
                                 'holding_seconds': str(duration), 'fill_count': len(current),
                                 'scale_in_fill_count': max(0, increases-1),
                                 'partial_reduce_fill_count': partial_reductions,
                                 'max_abs_position': str(max(Decimal(item['derived_end_position']).copy_abs() for item in current)),
                                 'event_ids': [item['event_id'] for item in current]})
                current, previous = [], None
        if current:
            reasons['RIGHT_CENSORED_OPEN_POSITION'] += 1
        assigned = sum(episode['fill_count'] for episode in episodes)
        hours = Counter(datetime.fromtimestamp(fill['time_ms']/1000, timezone.utc).strftime('%H') for fill in rows)
        results[coin] = {'sample_coverage': {'pagination_truncated': pagination_truncated, 'requested_window_complete': False},
                         'fill_count': len(rows), 'complete_zero_to_zero_episode_count': len(episodes),
                         'unmatched_or_unknown_fill_count': len(rows)-assigned,
                         'position_continuity_break_count': breaks, 'unknown_boundary_reasons': dict(reasons),
                         'complete_episodes': episodes,
                         'complete_episode_holding_seconds_quantiles': quantiles([Decimal(item['holding_seconds']) for item in episodes]),
                         'fill_size_quantiles_source_units': quantiles([Decimal(fill['size']) for fill in rows]),
                         'utc_hour_fill_counts': dict(sorted(hours.items())),
                         'hypothesis_status': 'OBSERVED_BEHAVIOR_ONLY_NO_ENTRY_RULE_OR_RETURN_INFERENCE'}
    return results


def describe(fills, *, pagination_truncated=None):
    fees, pnl = defaultdict(Decimal), defaultdict(Decimal)
    with localcontext() as precision:
        precision.prec = 200
        for fill in fills:
            fees[fill['fee_token']] += Decimal(fill['fee'])
            pnl[fill['coin']] += Decimal(fill['reported_closed_pnl'])
    return {'observed_fill_count': len(fills),
            'reported_direction_counts': dict(Counter(fill['reported_direction'] for fill in fills)),
            'reported_fees_by_token': {key: str(value) for key, value in sorted(fees.items())},
            'reported_closed_pnl_by_coin': {key: str(value) for key, value in sorted(pnl.items())},
            'per_coin_behavior': behavior_features(fills, pagination_truncated),
            'inference_status': 'BEHAVIOR_ONLY_NOT_RECOVERED_STRATEGY',
            'limitations': ['Fills do not reveal entry indicators, hidden orders, stops, external hedges, or news filters.',
                            'PnL is source-reported closed PnL, not audited account return or net portfolio profit.',
                            'Funding, deposits, withdrawals, starting inventory and unrealized PnL are absent.',
                            'Fees stay grouped by token; builder fees are preserved separately, not subtracted twice.',
                            'Public addresses are accounts; human identity and ownership are not verified.']}


class TraderObserver:
    def __init__(self, client=None, clock=None):
        self.owned = client is None
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)
        self.clock = clock or (lambda: int(time.time()*1000))
        self.receipts = []
        self.raw_fill_responses = {}

    def close(self):
        if self.owned:
            self.client.close()

    def _request(self, method, url, payload=None, *, byte_limit):
        receipt = {'url': url, 'method': method, 'request': payload}
        try:
            with self.client.stream(method, url, json=payload) as response:
                receipt['http_status'] = response.status_code
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > byte_limit:
                        raise ObservationError('PUBLIC_RESPONSE_TOO_LARGE')
        except httpx.TimeoutException as error:
            receipt.update(error='PUBLIC_TIMEOUT_NO_RETRY', fetched_at_utc=utc(self.clock()))
            self.receipts.append(receipt)
            raise ObservationError('PUBLIC_TIMEOUT_NO_RETRY') from error
        except httpx.HTTPError as error:
            receipt.update(error='PUBLIC_TRANSPORT_UNAVAILABLE', fetched_at_utc=utc(self.clock()))
            self.receipts.append(receipt)
            raise ObservationError('PUBLIC_TRANSPORT_UNAVAILABLE') from error
        except ObservationError as error:
            receipt.update(error=str(error), fetched_at_utc=utc(self.clock()),
                           received_prefix_bytes=len(body), complete_body=False,
                           prefix_sha256=hashlib.sha256(body).hexdigest())
            self.receipts.append(receipt)
            raise
        fetched = self.clock()
        receipt.update(fetched_at_ms=fetched, fetched_at_utc=utc(fetched),
                       response_bytes=len(body), raw_sha256=hashlib.sha256(body).hexdigest())
        self.receipts.append(receipt)
        if url == INFO_URL:
            self.raw_fill_responses[receipt['raw_sha256']] = bytes(body)
        if response.status_code == 429:
            raise ObservationError('RATE_LIMITED_429_NO_RETRY')
        if response.status_code != 200:
            raise ObservationError(f'PUBLIC_HTTP_{response.status_code}')
        try:
            data = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ObservationError('INVALID_PUBLIC_JSON') from error
        return data, receipt

    def discover(self, limit=3):
        if type(limit) is not int or not 1 <= limit <= 3:
            raise ObservationError('AT_MOST_THREE_PUBLIC_ACCOUNTS')
        data, receipt = self._request('GET', LEADERBOARD_URL, byte_limit=64*1024*1024)
        if not isinstance(data, dict) or not isinstance(data.get('leaderboardRows'), list):
            raise ObservationError('INVALID_LEADERBOARD')
        selected, seen = [], set()
        for index, row in enumerate(data['leaderboardRows']):
            if not isinstance(row, dict):
                continue
            address = row.get('ethAddress')
            if not isinstance(address, str) or not ADDRESS.fullmatch(address) or address.lower() in seen:
                continue
            try:
                windows = row['windowPerformances']
                if not isinstance(windows, list):
                    continue
                day = next(value for name, value in windows if name == 'day')
                if decimal_text(day['vlm']) <= 0:
                    continue
                decimal_text(row['accountValue'], positive=True)
            except (KeyError, TypeError, ValueError, StopIteration):
                continue
            seen.add(address.lower())
            # Preserve only the bounded selected public addresses, not display names or the whole board.
            selected.append({'address': address.lower(), 'discovery_row_index': index,
                             'account_value_reported': row['accountValue'], 'day_volume_reported': day['vlm']})
            if len(selected) == limit:
                break
        if not selected:
            raise ObservationError('NO_PUBLIC_ACTIVE_ACCOUNTS_DISCOVERED')
        return {'receipt': receipt, 'board_row_count': len(data['leaderboardRows']), 'selected': selected,
                'selection': 'First unique listed accounts with positive reported day volume and account value; illustrative, not a recommendation.',
                'contract': 'Official-domain public CDN snapshot; not a documented stable API and not proof of ranking freshness.'}

    def fills(self, trader, start, end, max_pages=3):
        now = self.clock()
        if not isinstance(trader, str) or not ADDRESS.fullmatch(trader):
            raise ObservationError('INVALID_PUBLIC_ACCOUNT')
        if (any(type(value) is not int for value in (start, end, max_pages)) or
                not 0 <= start <= end <= now or end-start > 7*86400000 or not 1 <= max_pages <= 3):
            raise ObservationError('INVALID_BOUNDED_WINDOW_OR_PAGE_BUDGET')
        trader = trader.lower()
        seen, pages, cursor = {}, [], start
        exhausted, reason = False, 'PAGE_BUDGET_REACHED'
        for _ in range(max_pages):
            request = {'type': 'userFillsByTime', 'user': trader, 'startTime': cursor,
                       'endTime': end, 'aggregateByTime': False}
            data, receipt = self._request('POST', INFO_URL, request, byte_limit=4*1024*1024)
            if not isinstance(data, list) or len(data) > PAGE_LIMIT:
                raise ObservationError('INVALID_FILL_PAGE')
            pages.append({'receipt': receipt, 'raw_fills': data})
            if not data:
                exhausted, reason = True, 'EMPTY_API_PAGE'
                break
            parsed = [normalize_fill(row, trader, cursor, end, self.clock()) for row in data]
            newest = max(fill['time_ms'] for fill in parsed)
            added = 0
            for fill in parsed:
                old = seen.get(fill['event_id'])
                if old is not None and old != fill:
                    raise ObservationError('CONFLICTING_DUPLICATE_FILL')
                if old is None:
                    seen[fill['event_id']] = fill
                    added += 1
            if newest == cursor:
                if len(data) >= PAGE_LIMIT:
                    reason = 'SATURATED_TIMESTAMP_BOUNDARY'
                elif added == 0:
                    exhausted, reason = True, 'OVERLAP_ONLY_API_TAIL'
                else:
                    # A second inclusive request is needed to prove whether this tail progresses.
                    continue
                break
            cursor = newest  # Inclusive overlap avoids skipping distinct fills sharing one millisecond.
        ordered = sorted(seen.values(), key=lambda fill: (fill['time_ms'], fill['event_id']))
        return {'trader': trader, 'requested_start_ms': start, 'requested_start_utc': utc(start),
                'requested_end_ms': end, 'requested_end_utc': utc(end),
                'observed_first_ms': ordered[0]['time_ms'] if ordered else None,
                'observed_last_ms': ordered[-1]['time_ms'] if ordered else None,
                'pages': pages, 'fills': ordered, 'summary': describe(ordered, pagination_truncated=not exhausted),
                'coverage': {'api_tail_exhausted': exhausted, 'pagination_truncated': not exhausted,
                             'stop_reason': reason, 'requested_window_complete': False,
                             'retention_limit_reported_by_api': RETENTION_LIMIT,
                             'reason_complete_history_not_claimed': 'The API exposes only the most recent 10000 fills; older omissions cannot be disproved.'}}


def collect(output, *, traders=3, hours=24, max_pages=3):
    if type(hours) is not int or not 1 <= hours <= 168:
        raise ObservationError('WINDOW_REQUIRES_ONE_TO_168_HOURS')
    path = Path(output)
    path.mkdir(parents=True, exist_ok=False)
    observer = TraderObserver()
    end = observer.clock()
    report = {'mode': 'PUBLIC_READ_ONLY_SNAPSHOT', 'trading_enabled': False,
              'docs_url': DOCS_URL, 'requested_at_utc': utc(end), 'accounts': [],
              'gold_traders': {'status': 'NOT_CONNECTED',
                               'reason': 'No verified anonymous gold-broker fill API integrated; no MQL5/Myfxbook history or gold-token substitute is claimed.'}}
    try:
        report['discovery'] = observer.discover(traders)
        for candidate in report['discovery']['selected']:
            try:
                report['accounts'].append(observer.fills(candidate['address'], end-hours*3600000, end, max_pages))
            except ObservationError as error:
                report['accounts'].append({'trader': candidate['address'], 'status': 'UNAVAILABLE', 'error': str(error)})
    except ObservationError as error:
        report['discovery_error'] = str(error)
    finally:
        observer.close()
    raw_path = path/'responses'
    raw_path.mkdir()
    for receipt in observer.receipts:
        raw = observer.raw_fill_responses.get(receipt.get('raw_sha256'))
        if raw is not None:
            filename = receipt['raw_sha256']+'.json'
            (raw_path/filename).write_bytes(raw)
            receipt['raw_file'] = 'responses/'+filename
    report['request_receipts'] = observer.receipts
    report['finished_at_utc'] = utc(int(time.time()*1000))
    target = path/'observation.json'
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(target), 'accounts': len(report['accounts']),
                      'observed_fills': sum(len(item.get('fills', [])) for item in report['accounts']),
                      'discovery_error': report.get('discovery_error'), 'trading_enabled': False}))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New directory; refuses to overwrite existing evidence')
    parser.add_argument('--traders', type=int, default=3, choices=range(1, 4))
    parser.add_argument('--hours', type=int, default=24, choices=range(1, 169))
    parser.add_argument('--max-pages', type=int, default=3, choices=range(1, 4))
    arguments = parser.parse_args()
    collect(arguments.output, traders=arguments.traders, hours=arguments.hours, max_pages=arguments.max_pages)
