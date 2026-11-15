"""Explicit synthetic official-format fixtures; never production news injection."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from threading import Event as ThreadEvent, Thread
from time import perf_counter

import httpx
import pytest

from core.paper import public_news as module
from core.paper.directional import Config, entry_gate

NOW = datetime(2026, 11, 15, 22, tzinfo=timezone.utc)


def ics(*events):
    return 'BEGIN:VCALENDAR\nVERSION:2.0\nX-WR-TIMEZONE:America/New_York\n' + ''.join(
        'BEGIN:VEVENT\n' + value + '\nEND:VEVENT\n' for value in events) + 'END:VCALENDAR\n'


BLS = ics('UID:synthetic-bls\nDTSTART;TZID=US-Eastern:20261117T083000\nSUMMARY:Synthetic BLS release')
BEA = ics('UID:synthetic-bea\nDTSTART;VALUE=DATE-TIME:20261118T133000Z\nDTSTAMP:20261101T120000Z\nSUMMARY:Synthetic BEA release')
PROOF = '<h1>Schedule of Selected Releases for November 2026</h1>'
FED_PROOF = '<a href="/newsevents/2026-november.htm">November 2026</a>'
FED = {'events': [{'month': '2026-11', 'days': '19', 'time': '2:00 p.m.', 'title': 'Synthetic Fed release', 'type': 'Stat'}], 'announcement': []}


class SyntheticHTTP:
    def __init__(self):
        self.calls = []
        self.contents = {module.BLS_URL: BLS, module.BEA_URL: BEA, module.BEA_SCOPE_URL: '<th>Year 2026</th>',
                         module.FED_SCOPE_URL: FED_PROOF, module.FED_URL: json.dumps(FED),
                         'https://www.bls.gov/schedule/2026/11_sched.htm': PROOF}
        self.statuses, self.pages, self.overrides = {}, {}, {}
        self.date = NOW

    def __call__(self, request):
        self.calls.append(request)
        url = str(request.url).split('?', 1)[0]
        if url in self.overrides:
            return self.overrides[url](request)
        if url == module.BITGET_URL:
            category = request.url.params['annType']
            cursor = request.url.params.get('cursor')
            rows = self.pages.get((category, cursor), [])
            body = json.dumps({'code': '00000', 'requestTime': int(request.url.params['endTime']), 'data': rows})
        else:
            body = self.contents[url]
        return httpx.Response(self.statuses.get(url, 200), text=body, headers={
            'Date': format_datetime(self.date, usegmt=True), 'Last-Modified': 'Wed, 10 Jun 2026 16:56:37 GMT'})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv('DIRECTIONAL_BLS_RELAY_URL', raising=False)
    monkeypatch.setattr(module, 'VENDOR_INTERVAL', 0)
    upstream = SyntheticHTTP()
    opened = []
    def create():
        collector = module.PublicNews(tmp_path / 'synthetic-news.sqlite3', fetcher=httpx.MockTransport(upstream))
        opened.append(collector)
        return collector
    collector = create()
    yield collector, upstream, create
    for item in opened:
        item.close()


def risk_row(identity='999', category='security', **updates):
    value = {'annId': identity, 'annType': category, 'annTitle': 'Synthetic vendor risk',
             'annUrl': f'https://www.bitget.com/en/support/articles/{identity}',
             'cTime': str(int((NOW - timedelta(minutes=1)).timestamp() * 1000))}
    value.update(updates)
    return value


def risk_config(instrument='BTCUSDT', **updates):
    fields = dict(mode='RESEARCH_PAPER', instrument=instrument, quote_source='SYNTHETIC', news_sources=(module.SOURCE,),
        initial_cash='10000', tick_size='.01', quantity_step='.01', max_quantity='10', max_order_quote='1000',
        fee_rate='.001', slippage='.01', quote_max_age_seconds=60, decision_ttl_seconds=60,
        news_max_age_seconds=120, news_pre_seconds=1800, news_post_seconds=1800)
    fields.update(updates)
    return Config(**fields)


def test_warmup_nullable_contract_and_no_read_network(setup):
    collector, upstream, _ = setup
    assert collector.source == module.SOURCE
    status = collector.status(NOW)
    assert status['state'] == 'WARMUP' and status['next_high'] == []
    for field in ('observed_at', 'coverage_start', 'coverage_end', 'error'):
        assert status[field] is None
    assert collector.read('BTCUSDT', NOW) is None
    assert upstream.calls == []


def test_all_sources_proof_ranges_and_unified_events_are_required_for_ready(setup):
    collector, upstream, _ = setup
    upstream.pages[('security', None)] = [risk_row()]
    collector.refresh(NOW)
    snapshot = collector.read('BTCUSDT', NOW + timedelta(seconds=1))
    assert snapshot is not None
    assert snapshot == collector.read('XAU/USD', NOW + timedelta(seconds=1))
    assert snapshot.coverage_start == NOW - timedelta(hours=3), 'quiet weekend starts from explicit coverage, never first event'
    assert snapshot.coverage_end == datetime(2026, 12, 1, 5, tzinfo=timezone.utc)
    assert len(snapshot.events) == 4 and all(row.impact == 'HIGH' for row in snapshot.events)
    vendor = next(row for row in snapshot.events if row.event_id.startswith('bitget:'))
    assert 'BTCUSDT' in vendor.instruments and 'XAU/USD' not in vendor.instruments
    assert vendor.scheduled_at == NOW - timedelta(minutes=1)
    assert entry_gate(risk_config(), NOW + timedelta(seconds=1), snapshot) == 'HIGH_IMPACT_EVENT'
    assert entry_gate(risk_config('XAU/USD'), NOW + timedelta(seconds=1), snapshot) == 'CLEAR'
    status = collector.status(NOW + timedelta(seconds=1))
    assert status['state'] == 'READY' and len(status['feeds']) == 9 and len(status['next_high']) <= 10
    assert all(row['sha256'] and row['upstream_date'] and row['last_modified'] for row in status['feeds'])
    assert 'FRED' not in status['scope'] and '纽约时间发布惯例（假设）' in status['scope']


def test_success_observation_never_renews_on_read_or_status_and_expires(setup):
    collector, upstream, _ = setup
    collector.refresh(NOW)
    first = collector.read('BTCUSDT', NOW + timedelta(seconds=1))
    calls = len(upstream.calls)
    later = collector.read('BTCUSDT', NOW + timedelta(seconds=90))
    assert first.observed_at == later.observed_at
    assert collector.status(NOW + timedelta(seconds=90))['observed_at'] == first.observed_at.isoformat()
    assert len(upstream.calls) == calls
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=121)) is None
    assert collector.status(NOW + timedelta(seconds=121))['state'] == 'STALE'


@pytest.mark.parametrize('status', [302, 403, 429, 500])
def test_failed_source_does_not_reuse_old_ready_permission_or_new_timestamp(setup, status):
    collector, upstream, _ = setup
    collector.refresh(NOW)
    before = collector.status(NOW + timedelta(seconds=1))
    upstream.statuses[module.BLS_URL] = status
    collector.refresh(NOW + timedelta(seconds=60))
    failed = collector.status(NOW + timedelta(seconds=61))
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=61)) is None
    assert failed['state'] == 'UNAVAILABLE' and failed['error'] == f'HTTP_{status}'
    assert failed['observed_at'] == before['observed_at']


@pytest.mark.parametrize('contents', ['', 'BEGIN:VCALENDAR\nBEGIN:VEVENT\n', ics(),
    ics('UID:x\nSUMMARY:Broken\nDTSTART:20261117T999900Z'),
    ics('UID:x\nSUMMARY:Recurring\nDTSTART:20261117T133000Z\nRRULE:FREQ=DAILY')])
def test_empty_truncated_or_ambiguous_calendar_never_grants_permission(setup, contents):
    collector, upstream, _ = setup
    upstream.contents[module.BLS_URL] = contents
    collector.refresh(NOW)
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None
    assert collector.status(NOW + timedelta(seconds=1))['state'] == 'UNAVAILABLE'


@pytest.mark.parametrize(('stamp', 'expected_hour'), [('20260701T083000', 12), ('20260115T083000', 13)])
def test_bls_eastern_dst_is_converted_using_zoneinfo(stamp, expected_hour):
    rows, _ = module._parse_ics(ics(f'UID:dst\nSUMMARY:Synthetic DST\nDTSTART;TZID=US-Eastern:{stamp}'), 'bls', module.BLS_URL, NOW)
    assert rows[0].scheduled_at.hour == expected_hour


@pytest.mark.parametrize('stamp', ['20260308T023000', '20261101T013000'])
def test_dst_nonexistent_or_ambiguous_wall_clock_is_rejected(stamp):
    with pytest.raises(ValueError, match='CALENDAR_TIME_AMBIGUOUS'):
        module._parse_ics(ics(f'UID:dst\nSUMMARY:Synthetic DST\nDTSTART;TZID=US-Eastern:{stamp}'), 'bls', module.BLS_URL, NOW)


def test_ics_unfolding_utc_and_duplicate_identity_validation():
    raw = 'UID:same\nSUMMARY:Synthetic long\n title\nDTSTART:20261117T133000Z\nDTSTAMP:20261101T120000Z'
    events, labels = module._parse_ics(ics(raw, raw), 'bea', module.BEA_URL, NOW)
    assert len(events) == 1 and list(labels.values())[0]['title'] == 'Synthetic longtitle'
    assert events[0].scheduled_at.hour == 13 and events[0].known_at.day == 1
    with pytest.raises(ValueError, match='CALENDAR_DUPLICATE'):
        module._parse_ics(ics(raw, raw.replace('133000', '143000')), 'bea', module.BEA_URL, NOW)


@pytest.mark.parametrize('source', ['bls', 'bea', 'fed'])
def test_no_event_minimum_or_guessed_year_replaces_explicit_scope_proof(setup, source):
    collector, upstream, _ = setup
    url = {'bls': 'https://www.bls.gov/schedule/2026/11_sched.htm', 'bea': module.BEA_SCOPE_URL, 'fed': module.FED_SCOPE_URL}[source]
    upstream.contents[url] = '<p>There are events on November 17</p>'
    collector.refresh(NOW)
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None


def test_unknown_fed_time_has_no_fake_midnight_event_and_blocks_current_whole_day(setup):
    collector, upstream, _ = setup
    value = json.loads(json.dumps(FED));value['events'].append({'month': '2026-11', 'days': '15', 'time': '', 'title': 'Synthetic unknown time'})
    upstream.contents[module.FED_URL] = json.dumps(value)
    collector.refresh(NOW)
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None
    status = collector.status(NOW + timedelta(seconds=1))
    assert status['state'] == 'UNAVAILABLE' and status['error'] == 'CALENDAR_TIME_AMBIGUOUS'
    assert not any(row['title'] == 'Synthetic unknown time' for row in status['next_high'])


def test_unknown_future_multiday_range_clips_permission_at_first_local_day(setup):
    collector, upstream, _ = setup
    value = json.loads(json.dumps(FED));value['events'].append({'month': '2026-11', 'days': '16-17', 'time': '2:00 p.m.', 'title': 'Synthetic multiday'})
    upstream.contents[module.FED_URL] = json.dumps(value)
    collector.refresh(NOW)
    snapshot = collector.read('BTCUSDT', NOW + timedelta(seconds=1))
    assert snapshot.coverage_end == datetime(2026, 11, 16, 5, tzinfo=timezone.utc)
    assert entry_gate(risk_config(news_pre_seconds=28800), NOW + timedelta(seconds=1), snapshot) == 'NEWS_COVERAGE_INCOMPLETE'


def test_vendor_full_page_requires_cursor_tail_and_publication_time_not_title_guess(setup):
    collector, upstream, _ = setup
    upstream.pages[('security', None)] = [risk_row(str(value)) for value in range(100, 90, -1)]
    upstream.pages[('security', '91')] = [risk_row('90', annTitle='SYNTHETIC GOLD title does not target gold')]
    collector.refresh(NOW)
    snapshot = collector.read('BTCUSDT', NOW + timedelta(seconds=1))
    assert snapshot is not None and len([row for row in snapshot.events if row.event_id.startswith('bitget:')]) == 11
    requests = [row for row in upstream.calls if row.url.host == 'api.bitget.com' and row.url.params['annType'] == 'security']
    assert len(requests) == 2 and requests[1].url.params['cursor'] == '91'
    assert all('XAU/USD' not in row.instruments for row in snapshot.events if row.event_id.startswith('bitget:'))


@pytest.mark.parametrize('kind', ['duplicate', 'wrong_category', 'future', 'stale_clock', 'bad_code', 'missing_data', 'page_limit'])
def test_vendor_invalid_or_incomplete_pages_fail_closed(setup, monkeypatch, kind):
    collector, upstream, _ = setup
    rows = [risk_row()]
    if kind == 'duplicate':rows = [risk_row(), risk_row()]
    elif kind == 'wrong_category':rows = [risk_row(category='symbol_delisting')]
    elif kind == 'future':rows = [risk_row(cTime=str(int((NOW + timedelta(seconds=1)).timestamp() * 1000)))]
    elif kind == 'page_limit':
        monkeypatch.setattr(module, 'MAX_PAGES', 1)
        rows = [risk_row(str(value)) for value in range(100, 90, -1)]
    upstream.pages[('security', None)] = rows
    if kind in ('stale_clock', 'bad_code', 'missing_data'):
        def broken(request):
            value = {'code': 'ERROR' if kind == 'bad_code' else '00000', 'requestTime': 1 if kind == 'stale_clock' else int(NOW.timestamp() * 1000), 'data': []}
            if kind == 'missing_data':value.pop('data')
            return httpx.Response(200, json=value, headers={'Date': format_datetime(NOW, usegmt=True)})
        upstream.overrides[module.BITGET_URL] = broken
    collector.refresh(NOW)
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None
    assert collector.status(NOW + timedelta(seconds=1))['state'] == 'UNAVAILABLE'


def test_persistent_evidence_survives_restart_but_requires_fresh_success(setup):
    collector, _, create = setup
    collector.refresh(NOW)
    previous = collector.status(NOW + timedelta(seconds=1))
    restarted = create()
    assert restarted.status(NOW + timedelta(seconds=1))['observed_at'] == previous['observed_at']
    assert restarted.status(NOW + timedelta(seconds=1))['state'] == 'WARMUP'
    assert restarted.read('BTCUSDT', NOW + timedelta(seconds=1)) is None
    restarted.refresh(NOW + timedelta(seconds=60))
    assert restarted.read('BTCUSDT', NOW + timedelta(seconds=61)) is not None


def test_persistence_failure_cannot_publish_new_ready_or_renew_old_evidence(setup, monkeypatch):
    collector, _, _ = setup
    collector.refresh(NOW)
    old = collector.status(NOW + timedelta(seconds=1))
    def failed(value):raise OSError('SYNTHETIC persistence failure, never exposed')
    monkeypatch.setattr(collector, '_persist', failed)
    collector.refresh(NOW + timedelta(seconds=60))
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=61)) is None
    status = collector.status(NOW + timedelta(seconds=61))
    assert status['error'] == 'NEWS_PERSISTENCE_ERROR' and status['observed_at'] == old['observed_at']
    assert 'SYNTHETIC' not in json.dumps(status)


def test_slow_rest_and_late_close_do_not_block_reads_or_publish_after_close(setup):
    collector, upstream, _ = setup
    collector.refresh(NOW)
    entered, release = ThreadEvent(), ThreadEvent()
    first_url = 'https://www.bls.gov/schedule/2026/11_sched.htm'
    def slow(request):
        entered.set();release.wait(2)
        return httpx.Response(200, text=PROOF, headers={'Date': format_datetime(NOW, usegmt=True)})
    upstream.overrides[first_url] = slow
    worker = Thread(target=collector.refresh, args=(NOW + timedelta(seconds=60),))
    worker.start();assert entered.wait(1)
    start = perf_counter()
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=61)) is not None
    assert collector.status(NOW + timedelta(seconds=61))['state'] == 'READY'
    assert perf_counter() - start < .2
    count = len(upstream.calls)
    collector.close();release.set();worker.join(1)
    assert not worker.is_alive() and len(upstream.calls) == count
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=61)) is None
    assert collector.status(NOW + timedelta(seconds=61))['error'] == 'COLLECTOR_CLOSED'


def test_relay_preserves_original_bls_fetch_time_and_component_ttl(setup, monkeypatch):
    _, upstream, create = setup
    relay = 'https://fc666-test.vercel.app/api/public-calendar/bls'
    monkeypatch.setenv('DIRECTIONAL_BLS_RELAY_URL', relay)
    fetched = NOW - timedelta(seconds=60)
    resources = {}
    for key, url, body in [('calendar', module.BLS_URL, BLS), ('scope', 'https://www.bls.gov/schedule/2026/11_sched.htm', PROOF)]:
        resources[key] = {'url': url, 'content': body, 'sha256': module._sha(body.encode()), 'fetched_at': fetched.isoformat(),
            'upstream_date': format_datetime(fetched, usegmt=True), 'last_modified': 'Wed, 10 Jun 2026 16:56:37 GMT', 'age': '10'}
    upstream.overrides[relay] = lambda request: httpx.Response(200, json={'version': 'bls-subscription-v1',
        'scope_month': '2026-11', 'resources': resources}, headers={'Date': format_datetime(NOW, usegmt=True)})
    collector = create();collector.refresh(NOW)
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is not None
    bls = next(row for row in collector.status(NOW + timedelta(seconds=1))['feeds'] if row['name'] == 'bls-calendar')
    assert bls['checked_at'] == fetched.isoformat()
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=61)) is None
    assert collector.status(NOW + timedelta(seconds=61))['error'] == 'NEWS_COMPONENT_STALE'


@pytest.mark.parametrize('fault', ['hash', 'source', 'month', 'stale'])
def test_relay_cannot_relabel_bad_source_or_old_content_as_fresh(setup, monkeypatch, fault):
    _, upstream, create = setup
    relay = 'https://fc666-test.vercel.app/api/public-calendar/bls'
    monkeypatch.setenv('DIRECTIONAL_BLS_RELAY_URL', relay)
    resource = {'url': module.BLS_URL, 'content': BLS, 'sha256': module._sha(BLS.encode()),
        'fetched_at': (NOW - timedelta(seconds=121) if fault == 'stale' else NOW).isoformat(),
        'upstream_date': format_datetime(NOW, usegmt=True), 'last_modified': None, 'age': None}
    scope = {**resource, 'url': 'https://www.bls.gov/schedule/2026/11_sched.htm', 'content': PROOF, 'sha256': module._sha(PROOF.encode())}
    if fault == 'hash':resource['sha256'] = '0' * 64
    if fault == 'source':resource['url'] = 'https://example.invalid/not-bls'
    upstream.overrides[relay] = lambda request: httpx.Response(200, json={'version': 'bls-subscription-v1',
        'scope_month': '2026-10' if fault == 'month' else '2026-11', 'resources': {'calendar': resource, 'scope': scope}},
        headers={'Date': format_datetime(NOW, usegmt=True)})
    collector = create();collector.refresh(NOW)
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None


@pytest.mark.parametrize('fault', ['stale_date', 'old_age', 'missing_date', 'future_date'])
def test_http_200_requires_current_clock_evidence_not_only_old_calendar_contents(setup, fault):
    collector, upstream, _ = setup
    headers = {'Date': format_datetime(NOW, usegmt=True), 'Last-Modified': 'Wed, 10 Jun 2026 16:56:37 GMT'}
    if fault == 'stale_date':headers['Date'] = format_datetime(NOW - timedelta(seconds=121), usegmt=True)
    if fault == 'old_age':headers['Age'] = '121'
    if fault == 'missing_date':headers.pop('Date')
    if fault == 'future_date':headers['Date'] = format_datetime(NOW + timedelta(seconds=6), usegmt=True)
    upstream.overrides[module.BLS_URL] = lambda request: httpx.Response(200, text=BLS, headers=headers)
    collector.refresh(NOW)
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None


def test_old_last_modified_is_legal_but_http_age_still_expires_at_original_budget(setup):
    collector, upstream, _ = setup
    upstream.overrides[module.BLS_URL] = lambda request: httpx.Response(200, text=BLS, headers={
        'Date': format_datetime(NOW, usegmt=True), 'Last-Modified': 'Wed, 10 Jun 2026 16:56:37 GMT', 'Age': '47'})
    collector.refresh(NOW)
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=60)) is not None
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=74)) is None
    assert collector.status(NOW + timedelta(seconds=74))['error'] == 'NEWS_COMPONENT_STALE'


def test_oversized_http_body_and_hidden_redirects_fail_closed(setup, monkeypatch):
    collector, upstream, _ = setup
    monkeypatch.setattr(module, 'MAX_BODY', 500)
    upstream.contents[module.BLS_URL] = BLS + (' ' * 501)
    collector.refresh(NOW)
    assert collector.status(NOW + timedelta(seconds=1))['error'] == 'HTTP_BODY_OVERSIZED'
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None


def test_slow_sqlite_commit_does_not_lock_read_or_status(setup, monkeypatch):
    collector, _, _ = setup
    collector.refresh(NOW)
    entered, release = ThreadEvent(), ThreadEvent()
    original = collector._persist
    def slow(value):
        entered.set();release.wait(2);original(value)
    monkeypatch.setattr(collector, '_persist', slow)
    worker = Thread(target=collector.refresh, args=(NOW + timedelta(seconds=60),))
    worker.start();assert entered.wait(1)
    started = perf_counter()
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=61)) is not None
    assert collector.status(NOW + timedelta(seconds=61))['state'] == 'READY'
    assert perf_counter() - started < .2
    release.set();worker.join(1)
    assert not worker.is_alive()


def test_relay_current_fetch_time_cannot_renew_stale_original_http_date(setup, monkeypatch):
    _, upstream, create = setup
    relay = 'https://fc666-test.vercel.app/api/public-calendar/bls'
    monkeypatch.setenv('DIRECTIONAL_BLS_RELAY_URL', relay)
    resources = {}
    for key, url, body in [('calendar', module.BLS_URL, BLS), ('scope', 'https://www.bls.gov/schedule/2026/11_sched.htm', PROOF)]:
        resources[key] = {'url': url, 'content': body, 'sha256': module._sha(body.encode()), 'fetched_at': NOW.isoformat(),
            'upstream_date': format_datetime(NOW - timedelta(seconds=121), usegmt=True), 'last_modified': None, 'age': '0'}
    upstream.overrides[relay] = lambda request: httpx.Response(200, json={'version': 'bls-subscription-v1',
        'scope_month': '2026-11', 'resources': resources}, headers={'Date': format_datetime(NOW, usegmt=True)})
    collector = create();collector.refresh(NOW)
    assert collector.status(NOW + timedelta(seconds=1))['error'] == 'HTTP_EVIDENCE_STALE'
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None


def test_fed_comma_enumerated_exact_release_times_are_not_guessed_all_day_risks(setup):
    collector, upstream, _ = setup
    value = json.loads(json.dumps(FED))
    value['events'] = [{'month': '2026-11', 'days': '15,16,17', 'time': '4:15 p.m.', 'title': 'Synthetic daily dates'}]
    upstream.contents[module.FED_URL] = json.dumps(value)
    collector.refresh(NOW)
    snapshot = collector.read('BTCUSDT', NOW + timedelta(seconds=1))
    assert snapshot is not None
    scheduled = [row.scheduled_at for row in snapshot.events if row.event_id.startswith('fed:')]
    assert scheduled == [datetime(2026, 11, day, 21, 15, tzinfo=timezone.utc) for day in (15, 16, 17)]
    assert collector.status(NOW + timedelta(seconds=1))['uncertain_windows'] == []


def test_one_required_failure_still_collects_all_other_official_feeds_without_permission(setup):
    collector, upstream, _ = setup
    upstream.statuses[module.BLS_URL] = 403
    collector.refresh(NOW)
    state = collector.status(NOW + timedelta(seconds=1))
    assert state['state'] == 'UNAVAILABLE' and collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None
    assert {str(row.url).split('?', 1)[0] for row in upstream.calls} >= {module.BEA_URL, module.FED_URL, module.BITGET_URL}
    assert next(row for row in state['feeds'] if row['name'] == 'bea-calendar')['events'] == 1
    assert next(row for row in state['feeds'] if row['name'] == 'fed-calendar')['events'] == 1
    assert {row['name'] for row in state['feeds']} >= {name for name, _ in module.FEEDS}
    assert next(row for row in state['feeds'] if row['name'] == 'bls-calendar')['error'] == 'HTTP_403'
    assert not next(row for row in state['feeds'] if row['name'] == 'bea-calendar').get('error')


def test_vendor_first_category_failure_does_not_skip_other_categories(setup):
    collector, upstream, _ = setup
    upstream.pages[('security', None)] = [risk_row(category='wrong')]
    collector.refresh(NOW)
    called = {row.url.params['annType'] for row in upstream.calls if row.url.host == 'api.bitget.com'}
    assert called == set(module.CATEGORIES)
    assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None


def test_injected_hidden_redirect_response_cannot_change_fixed_source(tmp_path, monkeypatch):
    monkeypatch.delenv('DIRECTIONAL_BLS_RELAY_URL', raising=False)
    def redirected(url, params=None):
        return httpx.Response(200, text=PROOF, request=httpx.Request('GET', 'https://example.invalid/not-official'),
                              headers={'Date': format_datetime(NOW, usegmt=True)})
    collector = module.PublicNews(tmp_path / 'synthetic-redirect.sqlite3', fetcher=redirected)
    try:
        collector.refresh(NOW)
        assert collector.read('BTCUSDT', NOW + timedelta(seconds=1)) is None
        assert collector.status(NOW + timedelta(seconds=1))['error'] == 'HTTP_REDIRECT_FORBIDDEN'
    finally:
        collector.close()
