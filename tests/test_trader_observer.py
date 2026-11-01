from decimal import Decimal
import hashlib
import json

import httpx
import pytest

from core.research.trader_observer import (
    INFO_URL, LEADERBOARD_URL, ObservationError, TraderObserver, describe, normalize_fill,
)


USER = '0x'+'1'*40
NOW = 200000
START, END = 100000, 150000


def fill(tid=1, stamp=100010, **updates):
    return {'coin': 'BTC', 'side': 'B', 'px': '100', 'sz': '.1', 'time': stamp,
            'startPosition': '0', 'dir': 'Open Long', 'closedPnl': '0',
            'hash': '0x'+'a'*64, 'oid': 1000+tid, 'tid': tid,
            'fee': '.01', 'feeToken': 'USDC', **updates}


def observer(handler):
    return TraderObserver(httpx.Client(transport=httpx.MockTransport(handler)), clock=lambda: NOW)


def test_public_discovery_is_bounded_deduplicated_and_has_raw_hash():
    raw = json.dumps({'leaderboardRows': [
        {'ethAddress': USER, 'accountValue': '10', 'windowPerformances': [['day', {'vlm': '100'}]]},
        {'ethAddress': USER, 'accountValue': '10', 'windowPerformances': [['day', {'vlm': '100'}]]},
        {'ethAddress': '0x'+'2'*40, 'accountValue': '10', 'windowPerformances': [['day', {'vlm': '0'}]]},
        {'ethAddress': '0x'+'3'*40, 'accountValue': '12', 'windowPerformances': [['day', {'vlm': '50'}]]},
    ]}).encode()
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=raw)
    result = observer(handler).discover(3)
    assert len(result['selected']) == 2
    assert result['selected'][1]['discovery_row_index'] == 3
    assert result['receipt']['raw_sha256'] == hashlib.sha256(raw).hexdigest()
    assert str(calls[0].url) == LEADERBOARD_URL and calls[0].method == 'GET'
    assert not any(name in calls[0].headers for name in ('authorization', 'cookie'))
    with pytest.raises(ObservationError, match='THREE'):
        observer(handler).discover(4)


def test_inclusive_pagination_preserves_same_millisecond_and_deduplicates():
    pages = [[fill(1), fill(2, 100020)], [fill(2, 100020), fill(3, 100020)], [fill(2, 100020), fill(3, 100020)]]
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        assert request.method == 'POST' and str(request.url) == INFO_URL
        return httpx.Response(200, json=pages[len(calls)-1])
    result = observer(handler).fills(USER, START, END)
    assert len(result['fills']) == 3
    assert [body['startTime'] for body in calls] == [START, 100020, 100020]
    assert all(body['aggregateByTime'] is False and body['type'] == 'userFillsByTime' for body in calls)
    assert result['coverage']['api_tail_exhausted'] is True
    assert result['coverage']['requested_window_complete'] is False
    assert len(result['pages']) == 3 and all(page['receipt']['raw_sha256'] for page in result['pages'])


def test_page_budget_and_saturated_same_timestamp_are_not_complete():
    result = observer(lambda _: httpx.Response(200, json=[fill()])).fills(USER, START, END, 1)
    assert result['coverage']['pagination_truncated'] is True
    assert result['coverage']['stop_reason'] == 'PAGE_BUDGET_REACHED'
    result = observer(lambda _: httpx.Response(200, json=[fill(tid, START) for tid in range(2000)])).fills(USER, START, END)
    assert result['coverage']['stop_reason'] == 'SATURATED_TIMESTAMP_BOUNDARY'
    assert result['coverage']['pagination_truncated'] is True


def test_empty_is_empty_observation_not_profitable_or_complete_history():
    result = observer(lambda _: httpx.Response(200, json=[])).fills(USER, START, END)
    assert result['fills'] == [] and result['coverage']['api_tail_exhausted'] is True
    assert result['summary']['inference_status'] == 'BEHAVIOR_ONLY_NOT_RECOVERED_STRATEGY'
    assert result['coverage']['requested_window_complete'] is False


def test_sell_directions_use_start_position_and_fees_keep_units():
    rows = [fill(1, side='A', dir='Close Long', startPosition='.2', closedPnl='2'),
            fill(2, side='A', dir='Open Short', startPosition='0', fee='-.001'),
            fill(3, side='B', dir='Close Short', startPosition='-.1', feeToken='HYPE', fee='.002'),
            fill(4, side='A', dir='Long > Short', startPosition='.05')]
    parsed = [normalize_fill(row, USER, START, END, NOW) for row in rows]
    assert [row['reported_direction'] for row in parsed] == ['Close Long', 'Open Short', 'Close Short', 'Long > Short']
    assert parsed[0]['derived_end_position'] == '0.1'
    assert parsed[1]['derived_end_position'] == '-0.1'
    assert parsed[3]['derived_end_position'] == '-0.05'
    summary = describe(parsed)
    assert Decimal(summary['reported_fees_by_token']['USDC']) == Decimal('.019')
    assert Decimal(summary['reported_fees_by_token']['HYPE']) == Decimal('.002')
    assert summary['reported_closed_pnl_by_coin']['BTC'] == '2'
    spot = normalize_fill(fill(dir='Sell', side='A', startPosition='1'), USER, START, END, NOW)
    assert spot['direction_status'] == 'UNCLASSIFIED' and spot['derived_end_position'] is None


@pytest.mark.parametrize('changes', [
    {'px': 'NaN'}, {'sz': '-1'}, {'fee': True}, {'time': NOW+1}, {'time': START-1},
    {'time': 100010.0}, {'tid': True}, {'hash': 'invalid'}, {'side': 'SELL'},
    {'dir': 'Open Short', 'side': 'B'}, {'px': '1e-999999'}, {'closedPnl': 'Infinity'},
])
def test_invalid_or_future_fill_is_rejected(changes):
    api = observer(lambda _: httpx.Response(200, json=[fill(**changes)]))
    with pytest.raises(ObservationError):
        api.fills(USER, START, END)
    assert len(api.receipts) == 1 and api.receipts[0]['raw_sha256']


def test_conflicting_duplicate_rejects_financial_rewrite():
    api = observer(lambda _: httpx.Response(200, json=[fill(), fill(fee='.02')]))
    with pytest.raises(ObservationError, match='CONFLICTING_DUPLICATE'):
        api.fills(USER, START, END)


@pytest.mark.parametrize('status', [429, 500, 302])
def test_http_error_no_retry_or_redirect(status):
    calls = []
    api = observer(lambda request: (calls.append(request) or httpx.Response(status, text='Unavailable')))
    with pytest.raises(ObservationError, match='429|HTTP'):
        api.fills(USER, START, END)
    assert len(calls) == 1
    assert api.receipts[0]['http_status'] == status and api.receipts[0]['raw_sha256']


def test_timeout_is_recorded_no_retry():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('public source timeout', request=request)
    api = observer(handler)
    with pytest.raises(ObservationError, match='TIMEOUT_NO_RETRY'):
        api.fills(USER, START, END)
    assert len(calls) == 1 and api.receipts[0]['error'] == 'PUBLIC_TIMEOUT_NO_RETRY'


@pytest.mark.parametrize('payload', [b'invalid json', b'{"invalid":true}', b'[NaN]'])
def test_bad_response_never_becomes_empty_success(payload):
    with pytest.raises(ObservationError):
        observer(lambda _: httpx.Response(200, content=payload)).fills(USER, START, END)


@pytest.mark.parametrize('start,end,pages', [(START, NOW+1, 1), (END, START, 1), (START, END, 4), (True, END, 1)])
def test_bad_window_rejected_before_network(start, end, pages):
    api = observer(lambda _: pytest.fail('must not make a network request'))
    with pytest.raises(ObservationError):
        api.fills(USER, start, end, pages)


def test_response_byte_budget_records_only_incomplete_prefix():
    api = observer(lambda _: httpx.Response(200, content=b'12345'))
    with pytest.raises(ObservationError, match='TOO_LARGE'):
        api._request('GET', LEADERBOARD_URL, byte_limit=4)
    assert api.receipts[0]['complete_body'] is False
    assert 'prefix_sha256' in api.receipts[0] and 'raw_sha256' not in api.receipts[0]


def test_collect_saves_exact_fill_bytes_and_never_overwrites(tmp_path, monkeypatch):
    from core.research import trader_observer as module
    raw = b'[]'
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'leaderboardRows': [
                {'ethAddress': USER, 'accountValue': '10', 'windowPerformances': [['day', {'vlm': '100'}]]}]})
        return httpx.Response(200, content=raw)
    api = observer(handler)
    api.clock = lambda: 1793050000000
    monkeypatch.setattr(module, 'TraderObserver', lambda: api)
    report = module.collect(tmp_path/'snapshot', traders=1, hours=1, max_pages=1)
    receipt = report['request_receipts'][1]
    retained = (tmp_path/'snapshot'/receipt['raw_file']).read_bytes()
    assert retained == raw and hashlib.sha256(retained).hexdigest() == receipt['raw_sha256']
    assert report['trading_enabled'] is False and report['gold_traders']['status'] == 'NOT_CONNECTED'
    with pytest.raises(FileExistsError):
        module.collect(tmp_path/'snapshot', traders=1, hours=1, max_pages=1)


def behavior(rows, truncated=True):
    parsed = [normalize_fill(row, USER, START, END, NOW) for row in rows]
    return describe(parsed, pagination_truncated=truncated)['per_coin_behavior']['BTC']


def test_learning_complete_long_episode_keeps_scale_in_and_partial_reduction():
    result = behavior([
        fill(1, START, sz='1'),
        fill(2, START+10000, sz='1', startPosition='1'),
        fill(3, START+20000, sz='.5', side='A', dir='Close Long', startPosition='2'),
        fill(4, START+30000, sz='1.5', side='A', dir='Close Long', startPosition='1.5'),
    ])
    assert result['complete_zero_to_zero_episode_count'] == 1
    episode = result['complete_episodes'][0]
    assert episode['side'] == 'LONG' and episode['holding_seconds'] == '30'
    assert episode['scale_in_fill_count'] == 1 and episode['partial_reduce_fill_count'] == 1
    assert episode['max_abs_position'] == '2' and result['unmatched_or_unknown_fill_count'] == 0
    assert result['sample_coverage']['pagination_truncated'] is True


def test_learning_complete_short_episode_and_same_millisecond_trade_ids():
    result = behavior([
        fill(9, START, side='A', dir='Open Short', sz='.2'),
        fill(10, START, side='B', dir='Close Short', startPosition='-.2', sz='.1'),
        fill(11, START+20000, side='B', dir='Close Short', startPosition='-.1', sz='.1'),
    ])
    assert result['complete_zero_to_zero_episode_count'] == 1
    episode = result['complete_episodes'][0]
    assert episode['side'] == 'SHORT' and episode['holding_seconds'] == '20'
    assert episode['partial_reduce_fill_count'] == 1 and episode['scale_in_fill_count'] == 0
    assert result['position_continuity_break_count'] == 0


def test_learning_window_existing_position_is_not_invented_episode():
    result = behavior([
        fill(1, START, side='A', dir='Close Long', startPosition='.1'),
        fill(2, START+10000),
        fill(3, START+20000, side='A', dir='Close Long', startPosition='.1'),
    ])
    assert result['complete_zero_to_zero_episode_count'] == 1
    assert result['unmatched_or_unknown_fill_count'] == 1
    assert result['complete_episodes'][0]['start_time_ms'] == START+10000
    assert result['unknown_boundary_reasons']['NO_OBSERVED_FLAT_START'] == 1


def test_learning_missing_fill_and_open_tail_do_not_make_complete_episodes():
    result = behavior([
        fill(1, START),
        fill(2, START+10000, side='A', dir='Close Long', startPosition='.3', sz='.3'),
        fill(3, START+20000),
    ])
    assert result['complete_zero_to_zero_episode_count'] == 0
    assert result['unmatched_or_unknown_fill_count'] == 3
    assert result['position_continuity_break_count'] == 1
    assert result['unknown_boundary_reasons']['RIGHT_CENSORED_OPEN_POSITION'] == 1
    assert result['complete_episode_holding_seconds_quantiles'] is None


def test_learning_cross_zero_and_unknown_spot_never_fabricate_entry_or_exit():
    result = behavior([
        fill(1, START),
        fill(2, START+10000, side='A', dir='Long > Short', startPosition='.1', sz='.2'),
        fill(3, START+20000, side='B', dir='Close Short', startPosition='-.1'),
        fill(4, START+30000, dir='Buy'),
    ])
    assert result['complete_zero_to_zero_episode_count'] == 0
    assert result['unmatched_or_unknown_fill_count'] == 4
    assert result['unknown_boundary_reasons']['CROSS_ZERO_WITHOUT_OBSERVED_FLAT'] == 1
    assert result['unknown_boundary_reasons']['UNCLASSIFIED_DIRECTION'] == 1
