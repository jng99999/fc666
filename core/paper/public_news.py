"""Bounded official calendars and published vendor risk, not a global news oracle.

Only the collector performs network IO. Read/status never refresh timestamps or
wait on REST/SQLite. Every grant follows a successful durable snapshot write.
"""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock, RLock
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from core.paper.directional import Event, News, aware
from core.paper.directional_market import INSTRUMENTS

SOURCE = 'official-us-calendar-bitget-v1'
VERSION = 'official-calendar-publication-v1'
MAX_BODY = 1500000
MAX_PAGES = 8
VENDOR_INTERVAL = .1
TTL = 120
LOOKBACK = timedelta(hours=3)
NY = ZoneInfo('America/New_York')
UTC = timezone.utc
BLS_URL = 'https://www.bls.gov/schedule/news_release/bls.ics'
BLS_SCOPE_URL = 'https://www.bls.gov/schedule/'
BEA_URL = 'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics'
BEA_SCOPE_URL = 'https://www.bea.gov/news/schedule'
FED_URL = 'https://www.federalreserve.gov/json/calendar.json'
FED_SCOPE_URL = 'https://www.federalreserve.gov/newsevents/calendar.htm'
NYFED_URL = 'https://www.newyorkfed.org/research/calendars/nationalecon_cal'
NYFED_SOURCE = 'official-nyfed-us-calendar-bitget-v2'
BITGET_URL = 'https://api.bitget.com/api/v2/public/annoucements'
CATEGORIES = ('security', 'maintenance_system_updates', 'symbol_delisting')
CRYPTO = tuple(name for name in INSTRUMENTS if name != 'XAU/USD')
SCOPE = ('BLS/BEA/Fed官方列举宏观日历及Bitget已发布风险公告，全部保守HIGH是本平台政策；'
         '不是全世界或所有突发新闻。Fed JSON未附时区，采用纽约时间发布惯例（假设）。'
         'Bitget仅回查3小时已发布公告及发布后冷却，不是未来维护日历；未来coverage仅表示已列宏观计划。')
FEEDS = (('bls-calendar', BLS_URL), ('bls-month-scope', BLS_SCOPE_URL), ('bea-calendar', BEA_URL), ('bea-year-scope', BEA_SCOPE_URL),
         ('fed-calendar', FED_URL), ('fed-month-scope', FED_SCOPE_URL),
         *((f'bitget-{category}', BITGET_URL) for category in CATEGORIES))
NYFED_SCOPE = ('纽约联储列举的整月关键经济发布日历、BEA/Fed日历及Bitget已发布风险公告；'
               '包含CPI、就业、PPI、JOLTS，但不是BLS全部目录或全球突发新闻。全部保守HIGH是本平台政策。'
               '纽约联储明确使用美国东部时间；无AM/PM的01至07点条目按整日不确定风险处理。'
               'Fed JSON未附时区，采用纽约发布惯例（假设）。Bitget仅回查3小时公告及发布后冷却。'
               '纽约联储日历经本平台解析转换，保留原始标题和链接；不代表其背书。'
               '使用条款：https://www.newyorkfed.org/privacy/termsofuse')


class _Failure(ValueError):
    pass


def _sha(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def _plain(value, maximum=1000):
    if not isinstance(value, str) or len(value) > maximum * 10:
        raise _Failure('CONTENT_INVALID')
    value = re.sub(r'<[^>]*>', ' ', unescape(value))
    value = ' '.join(value.split())
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise _Failure('CONTENT_INVALID')
    return value


class _HTML(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.text, self.links = [], []
        self.feed(text)
        self.close()

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(value for key, value in attrs if key == 'href' and value)


def _local(naive, zone):
    first, second = naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset() or first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive:
        raise _Failure('CALENDAR_TIME_AMBIGUOUS')
    return first.astimezone(UTC)


def _month(at, zone):
    local = at.astimezone(zone)
    start = datetime(local.year, local.month, 1)
    end = datetime(local.year + (local.month == 12), local.month % 12 + 1, 1)
    return _local(start, zone), _local(end, zone)


def _ics_time(key, value, default_zone='America/New_York'):
    if not re.fullmatch(r'\d{8}T\d{6}Z?', value):
        raise _Failure('CALENDAR_TIME_INVALID')
    naive = datetime.strptime(value.rstrip('Z'), '%Y%m%dT%H%M%S')
    if value.endswith('Z'):
        return naive.replace(tzinfo=UTC)
    match = re.search(r';TZID="?([^;"]+)', key, re.I)
    zone = match.group(1) if match else default_zone
    if zone not in ('US-Eastern', 'US/Eastern', 'America/New_York'):
        raise _Failure('CALENDAR_TIMEZONE_UNSUPPORTED')
    return _local(naive, NY)


def _parse_ics(text, name, url, checked_at):
    """Whole-file ICS validation; no RRULE expansion or guessed naive zones."""
    text = re.sub(r'\r?\n[ \t]', '', text.lstrip('\ufeff'))
    lines = text.splitlines()
    if not lines or lines[0] != 'BEGIN:VCALENDAR' or lines[-1] != 'END:VCALENDAR' or len(lines) > 30000:
        raise _Failure('CALENDAR_TRUNCATED')
    records, current, default_zone = [], None, 'America/New_York'
    for line in lines:
        if len(line) > 16000:
            raise _Failure('CALENDAR_OVERSIZED')
        if line == 'BEGIN:VEVENT':
            if current is not None:
                raise _Failure('CALENDAR_TRUNCATED')
            current = {}
        elif line == 'END:VEVENT':
            if current is None:
                raise _Failure('CALENDAR_TRUNCATED')
            records.append(current); current = None
        elif current is not None:
            if ':' not in line:
                raise _Failure('CALENDAR_INVALID')
            key, value = line.split(':', 1)
            base = key.split(';')[0]
            if base in ('RRULE', 'RDATE', 'EXDATE', 'RECURRENCE-ID'):
                raise _Failure('CALENDAR_RECURRENCE_UNSUPPORTED')
            if base in current:
                raise _Failure('CALENDAR_DUPLICATE')
            current[base] = (key, value)
        elif line.startswith('X-WR-TIMEZONE:'):
            default_zone = line.split(':', 1)[1]
    if current is not None or not records or len(records) > 5000:
        raise _Failure('CALENDAR_EMPTY_OR_TRUNCATED')
    events, labels = {}, {}
    for row in records:
        if any(key not in row for key in ('UID', 'SUMMARY', 'DTSTART')):
            raise _Failure('CALENDAR_FIELDS_MISSING')
        uid = row['UID'][1]
        if not uid or len(uid) > 1000:
            raise _Failure('CALENDAR_UID_INVALID')
        scheduled = _ics_time(*row['DTSTART'], default_zone)
        known = _ics_time(*row['DTSTAMP'], default_zone) if 'DTSTAMP' in row else checked_at
        if known > checked_at:
            raise _Failure('CALENDAR_STAMP_FROM_FUTURE')
        title = _plain(row['SUMMARY'][1].replace('\\,', ',').replace('\\;', ';').replace('\\n', ' ').replace('\\N', ' '))
        event_id = f'{name}:' + _sha(uid.encode())
        event = Event(event_id=event_id, instruments=('*',), impact='HIGH', scheduled_at=scheduled, known_at=known)
        if event_id in events and events[event_id] != event:
            raise _Failure('CALENDAR_DUPLICATE')
        events[event_id] = event
        labels[event_id] = {'title': title, 'url': url}
    return list(events.values()), labels


def _parse_fed(text, proof, now, checked_at):
    value = json.loads(text)
    local = now.astimezone(NY)
    month = local.strftime('%Y-%m')
    expected = f'/newsevents/{local.year}-{local.strftime("%B").lower()}.htm'
    if expected not in _HTML(proof).links or not isinstance(value, dict) or not isinstance(value.get('events'), list):
        raise _Failure('FED_MONTH_COVERAGE_MISSING')
    rows = [row for row in value['events'] if isinstance(row, dict) and row.get('month') == month]
    if not rows:
        raise _Failure('FED_CALENDAR_EMPTY')
    events, labels, uncertain, seen = [], {}, [], set()
    for row in rows:
        title = _plain(row.get('title'))
        days = row.get('days')
        if not isinstance(days, str) or not re.fullmatch(r'\s*\d{1,2}(\s*[-,]\s*\d{1,2})*\s*', days):
            raise _Failure('FED_DATE_AMBIGUOUS')
        time_text = row.get('time')
        if not isinstance(time_text, str):
            raise _Failure('FED_TIME_INVALID')
        parsed = re.fullmatch(r'\s*(\d{1,2}):(\d{2})\s*(a\.m\.|p\.m\.)\s*', time_text, re.I)
        if '-' in days:
            if not re.fullmatch(r'\s*\d{1,2}\s*-\s*\d{1,2}\s*', days):
                raise _Failure('FED_DATE_AMBIGUOUS')
            first, last = (int(day.strip()) for day in days.split('-'))
            if first >= last:
                raise _Failure('FED_DATE_AMBIGUOUS')
            dates = list(range(first, last + 1))
        else:
            dates = [int(day.strip()) for day in days.split(',')]
        uncertain_time = parsed is None or '-' in days or bool(re.search(r'approximate|tentative|\bTBD\b', str(row.get('description', '')), re.I))
        for day in dates:
            date = datetime(local.year, local.month, day)
            url = 'https://www.federalreserve.gov' + expected
            key = _sha([month, day, title, time_text, row.get('type'), uncertain_time])
            if key in seen:
                continue
            seen.add(key)
            if uncertain_time:
                uncertain.append({'start': _local(date, NY), 'end': _local(date + timedelta(days=1), NY), 'title': title, 'url': url})
            else:
                hour, minute = int(parsed[1]), int(parsed[2])
                if not 1 <= hour <= 12 or minute > 59:
                    raise _Failure('FED_TIME_INVALID')
                hour = hour % 12 + (12 if parsed[3].lower() == 'p.m.' else 0)
                scheduled = _local(date.replace(hour=hour, minute=minute), NY)
                event_id = 'fed:' + key
                events.append(Event(event_id=event_id, instruments=('*',), impact='HIGH', scheduled_at=scheduled, known_at=checked_at))
                labels[event_id] = {'title': title, 'url': url}
    # Month announcements can alter release dates; absent fields or new formats
    # cannot silently disappear from the current calendar scope.
    announcements = value.get('announcement', [])
    if not isinstance(announcements, list):
        raise _Failure('FED_ANNOUNCEMENTS_INVALID')
    for row in announcements:
        if not isinstance(row, dict):
            raise _Failure('FED_ANNOUNCEMENTS_INVALID')
        if row.get('month') == month:
            day = row.get('day')
            if not isinstance(day, str) or not day.isdigit():
                raise _Failure('FED_ANNOUNCEMENT_DATE_AMBIGUOUS')
            date = datetime(local.year, local.month, int(day))
            uncertain.append({'start': _local(date, NY), 'end': _local(date + timedelta(days=1), NY),
                              'title': _plain(row.get('title')), 'url': 'https://www.federalreserve.gov' + expected})
    return events, labels, uncertain, _month(now, NY)


def _feed(name, url):
    return {'name': name, 'url': url, 'checked_at': None, 'upstream_date': None,
            'last_modified': None, 'sha256': None, 'events': 0, 'age': None}


def _error_code(error):
    return str(error) if isinstance(error, _Failure) else 'SOURCE_PARSE_OR_TRANSPORT_ERROR'


def _http_metadata(date, age, modified, checked, *, fetched_at=None):
    try:
        if not isinstance(date, str):
            raise ValueError('Date required')
        upstream = aware(parsedate_to_datetime(date))
        if upstream > checked + timedelta(seconds=5):
            raise ValueError('Future server clock')
        if age is not None and (not isinstance(age, str) or not age.isascii() or not age.isdigit() or len(age) > 12):
            raise ValueError('Invalid HTTP Age')
        elapsed = max(0, (checked - fetched_at).total_seconds()) if fetched_at else 0
        if max((checked - upstream).total_seconds(), int(age or '0') + elapsed) > TTL:
            raise _Failure('HTTP_EVIDENCE_STALE')
        if modified is not None and (not isinstance(modified, str) or aware(parsedate_to_datetime(modified)) > checked + timedelta(seconds=5)):
            raise ValueError('Future resource version')
    except _Failure:
        raise
    except (ValueError, TypeError, OverflowError) as error:
        raise _Failure('HTTP_METADATA_INVALID') from error


class PublicNews:
    """Fetcher may be an httpx transport or callable ``(url, params=...) -> Response``."""
    source = SOURCE

    def __init__(self, path, *, fetcher=None, calendar='bls'):
        if calendar not in ('bls', 'nyfed'):
            raise ValueError('An explicit supported official calendar profile is required')
        self.calendar = calendar
        self.source = NYFED_SOURCE if calendar == 'nyfed' else SOURCE
        self.version = 'nyfed-calendar-publication-v2' if calendar == 'nyfed' else VERSION
        self.scope = NYFED_SCOPE if calendar == 'nyfed' else SCOPE
        # Refresh before a legitimate upstream cache Age consumes the 120s TTL.
        # Expiry remains unchanged; neither polling nor reading renews old data.
        self.poll_interval = 30 if calendar == 'nyfed' else 60
        self.feeds = (('nyfed-month-calendar', NYFED_URL), *FEEDS[2:]) if calendar == 'nyfed' else FEEDS
        self.path = Path(path)
        if self.path.suffix != '.sqlite3':
            raise ValueError('A dedicated .sqlite3 news evidence file is required')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
            except FileExistsError:
                pass
        self._lock, self._refresh_lock = RLock(), Lock()
        self._closed, self._last_vendor = False, 0.0
        self._relay_url = os.environ.get('DIRECTIONAL_BLS_RELAY_URL') or None
        if calendar == 'nyfed' and self._relay_url is not None:
            raise ValueError('The NY Fed profile cannot use a BLS subscription node')
        if self._relay_url is not None:
            parts = urlsplit(self._relay_url)
            if (self._relay_url != 'https://fc666-test.vercel.app/api/public-calendar/bls'
                    or parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                    or parts.port not in (None, 443) or parts.path != '/api/public-calendar/bls'
                    or parts.query or parts.fragment):
                raise ValueError('An explicit fixed HTTPS BLS subscription node is required')
        self._fetcher = fetcher if callable(fetcher) and not isinstance(fetcher, httpx.BaseTransport) else None
        self._client = None if self._fetcher else httpx.Client(transport=fetcher or httpx.HTTPTransport(retries=0, trust_env=False), trust_env=False,
            timeout=6, follow_redirects=False, headers={'User-Agent': 'FC666-public-source-research/1.0',
            'Accept': 'application/json,text/calendar,text/html;q=0.9', 'Cache-Control': 'no-cache'})
        self._state = {'version': self.version, 'state': 'WARMUP', 'error': None, 'snapshot': None,
                       'feeds': [_feed(name, url) for name, url in self.feeds]}
        with self._connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {'public_news_snapshot'}:
                raise ValueError('Refusing an unrelated evidence database')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS public_news_snapshot(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL,sha256 TEXT NOT NULL)')
            row = db.execute('SELECT payload,sha256 FROM public_news_snapshot WHERE id=1').fetchone()
        if row:
            try:
                value = json.loads(row[0])
                if len(row[0]) > 2500000 or _sha(value) != row[1] or value.get('version') != self.version:
                    raise ValueError('Invalid news evidence')
                if value.get('snapshot'):
                    if News.model_validate(value['snapshot']['news']).source != self.source:
                        raise ValueError('A different calendar profile cannot grant permission')
                self._state = {**value, 'state': 'WARMUP', 'error': 'REVALIDATION_REQUIRED'}
            except (ValueError, TypeError, KeyError):
                self._state.update(state='UNAVAILABLE', error='CACHE_INVALID')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=3)
        try:
            db.execute('PRAGMA synchronous=FULL')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _persist(self, state):
        payload = json.dumps(state, sort_keys=True, separators=(',', ':'))
        if len(payload) > 2500000:
            raise _Failure('NEWS_EVIDENCE_OVERSIZED')
        with self._connection() as db:
            db.execute('INSERT INTO public_news_snapshot VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,sha256=excluded.sha256', (payload, _sha(state)))

    def _stamp(self, now, started):
        return now + timedelta(seconds=max(0, time.monotonic() - started))

    def _request(self, name, url, now, started, feeds, params=None):
        with self._lock:
            if self._closed:
                raise _Failure('COLLECTOR_CLOSED')
        parsed = urlsplit(url)
        allowed = url in (BLS_URL, BEA_URL, BEA_SCOPE_URL, FED_URL, FED_SCOPE_URL, BITGET_URL, NYFED_URL, self._relay_url) or bool(re.fullmatch(r'https://www\.bls\.gov/schedule/\d{4}/\d{2}_sched\.htm', url))
        if not allowed or parsed.scheme != 'https' or parsed.query or parsed.fragment:
            raise _Failure('SOURCE_URL_INVALID')
        if parsed.netloc == 'api.bitget.com':
            delay = VENDOR_INTERVAL - (time.monotonic() - self._last_vendor)
            if delay > 0:
                time.sleep(delay)
            self._last_vendor = time.monotonic()
        with self._lock:
            if self._closed:
                raise _Failure('COLLECTOR_CLOSED')
        if self._fetcher:
            response = self._fetcher(url, params=params)
        else:
            self._client.cookies.clear()
            request = self._client.build_request('GET', url, params=params)
            request.headers.pop('Cookie', None)
            request.headers.pop('Authorization', None)
            response = self._client.send(request, stream=True)
        try:
            evidence = {'name': name, 'url': str(httpx.URL(url, params=params)),
                'checked_at': self._stamp(now, started).isoformat(), 'upstream_date': response.headers.get('Date'),
                'last_modified': response.headers.get('Last-Modified'), 'age': response.headers.get('Age'),
                'sha256': None, 'events': 0}
            feeds.append(evidence)
            if response.status_code != 200:
                raise _Failure(f'HTTP_{response.status_code}')
            try:
                if response.request.url != httpx.URL(url, params=params):
                    raise _Failure('HTTP_REDIRECT_FORBIDDEN')
            except RuntimeError:
                pass  # A synthetic injected Response may omit request metadata.
            raw = bytearray()
            for chunk in response.iter_bytes(chunk_size=16384):
                raw.extend(chunk)
                if len(raw) > MAX_BODY:
                    raise _Failure('HTTP_BODY_OVERSIZED')
            if not raw:
                raise _Failure('HTTP_BODY_EMPTY')
            checked = self._stamp(now, started)
            date = response.headers.get('Date')
            _http_metadata(date, response.headers.get('Age'), response.headers.get('Last-Modified'), checked)
            evidence.update(checked_at=checked.isoformat(), sha256=_sha(bytes(raw)))
            with self._lock:
                if self._closed:
                    raise _Failure('COLLECTOR_CLOSED')
            return bytes(raw).decode('utf-8-sig'), checked, evidence
        finally:
            response.close()

    def _resources(self, specs, now, started, feeds):
        results, failures = {}, []
        for name, url in specs:
            begin = len(feeds)
            try:
                results[name] = self._request(name, url, now, started, feeds)
            except Exception as error:
                code = _error_code(error)
                failures.append(code)
                if len(feeds) == begin:
                    feeds.append({**_feed(name, url), 'attempted_at': self._stamp(now, started).isoformat()})
                feeds[-1]['error'] = code
        return results, failures

    def _bls(self, now, started, feeds):
        local = now.astimezone(NY)
        scope_url = f'https://www.bls.gov/schedule/{local.year}/{local.month:02d}_sched.htm'
        if self._relay_url is None:
            resources, failures = self._resources((('bls-month-scope', scope_url), ('bls-calendar', BLS_URL)), now, started, feeds)
            if 'bls-calendar' in resources:
                text, checked, item = resources['bls-calendar']
                events, labels = _parse_ics(text, 'bls', BLS_URL, checked)
                item['events'] = len(events)
            if failures:
                raise _Failure(failures[0])
            proof = resources['bls-month-scope'][0]
        else:
            raw, verified_at, _ = self._request('bls-subscription-node', self._relay_url, now, started, feeds)
            value = json.loads(raw)
            if (not isinstance(value, dict) or value.get('version') != 'bls-subscription-v1'
                    or value.get('scope_month') != local.strftime('%Y-%m') or not isinstance(value.get('resources'), dict)):
                raise _Failure('BLS_RELAY_SCOPE_INVALID')
            parts = {}
            for key, name, url in [('scope', 'bls-month-scope', scope_url), ('calendar', 'bls-calendar', BLS_URL)]:
                resource = value['resources'].get(key)
                if not isinstance(resource, dict) or resource.get('url') != url or not isinstance(resource.get('content'), str):
                    raise _Failure('BLS_RELAY_RESOURCE_INVALID')
                body = resource['content'].encode()
                if not body or len(body) > MAX_BODY or _sha(body) != resource.get('sha256'):
                    raise _Failure('BLS_RELAY_HASH_INVALID')
                fetched = aware(datetime.fromisoformat(resource['fetched_at']))
                if not 0 <= (verified_at - fetched).total_seconds() <= TTL:
                    raise _Failure('BLS_RELAY_STALE')
                upstream = resource.get('upstream_date')
                modified, age = resource.get('last_modified'), resource.get('age')
                _http_metadata(upstream, age, modified, verified_at, fetched_at=fetched)
                item = {'name': name, 'url': url, 'checked_at': fetched.isoformat(), 'upstream_date': upstream,
                    'last_modified': modified, 'age': age, 'sha256': resource['sha256'], 'events': 0,
                    'subscription_node': self._relay_url}
                feeds.append(item)
                parts[key] = (resource['content'], fetched, item)
            proof = parts['scope'][0]
            text, checked, item = parts['calendar']
        expected = f'Schedule of Selected Releases for {local.strftime("%B")} {local.year}'
        if expected not in ' '.join(' '.join(_HTML(proof).text).split()):
            raise _Failure('BLS_MONTH_COVERAGE_MISSING')
        events, labels = _parse_ics(text, 'bls', BLS_URL, checked)
        item['events'] = len(events)
        return events, labels

    def _bea(self, now, started, feeds):
        resources, failures = self._resources((('bea-year-scope', BEA_SCOPE_URL), ('bea-calendar', BEA_URL)), now, started, feeds)
        if 'bea-calendar' in resources:
            text, checked, item = resources['bea-calendar']
            events, labels = _parse_ics(text, 'bea', BEA_URL, checked)
            item['events'] = len(events)
        if failures:
            raise _Failure(failures[0])
        years = {int(year) for year in re.findall(r'\bYear\s+(\d{4})\b', ' '.join(_HTML(resources['bea-year-scope'][0]).text))}
        if now.astimezone(NY).year not in years:
            raise _Failure('BEA_YEAR_COVERAGE_MISSING')
        return events, labels

    def _nyfed(self, now, started, feeds):
        from core.paper.nyfed_calendar import parse_nyfed
        text, checked, item = self._request('nyfed-month-calendar', NYFED_URL, now, started, feeds)
        events, labels, uncertain, coverage = parse_nyfed(text, now, checked)
        item['events'] = len(events)
        return events, labels, uncertain, coverage

    def _fed(self, now, started, feeds):
        resources, failures = self._resources((('fed-month-scope', FED_SCOPE_URL), ('fed-calendar', FED_URL)), now, started, feeds)
        if failures:
            raise _Failure(failures[0])
        text, checked, item = resources['fed-calendar']
        events, labels, uncertain, coverage = _parse_fed(text, resources['fed-month-scope'][0], now, checked)
        item['events'] = len(events)
        return events, labels, uncertain, coverage

    def _announcements(self, now, started, feeds, failures=None):
        errors = [] if failures is None else failures
        events, labels = [], {}
        for category in CATEGORIES:
            begin = len(feeds)
            try:
                rows, names = self._announcement_category(category, now, started, feeds)
                events.extend(rows); labels.update(names)
            except Exception as error:
                code = _error_code(error)
                errors.append(code)
                if len(feeds) == begin:
                    feeds.append(_feed(f'bitget-{category}', BITGET_URL))
                feeds[-1]['error'] = code
        if failures is None and errors:
            raise _Failure(errors[0])
        return events, labels, now - LOOKBACK

    def _announcement_category(self, category, now, started, feeds):
        end = int(now.timestamp() * 1000)
        start = end - int(LOOKBACK.total_seconds() * 1000)
        events, labels = [], {}
        for _category in (category,):
            cursor, seen = None, set()
            for page in range(MAX_PAGES):
                params = {'language': 'en_US', 'annType': category, 'startTime': str(start), 'endTime': str(end), 'limit': '10'}
                if cursor is not None:
                    params['cursor'] = cursor
                text, checked, evidence = self._request(f'bitget-{category}', BITGET_URL, now, started, feeds, params)
                value = json.loads(text)
                if not isinstance(value, dict) or value.get('code') != '00000' or not isinstance(value.get('data'), list) or len(value['data']) > 10:
                    raise _Failure('BITGET_ENVELOPE_INVALID')
                stamp = value.get('requestTime')
                if isinstance(stamp, bool) or not isinstance(stamp, (str, int)) or not str(stamp).isascii() or not str(stamp).isdigit():
                    raise _Failure('BITGET_CLOCK_INVALID')
                if abs(int(stamp) - int(checked.timestamp() * 1000)) > 60000:
                    raise _Failure('BITGET_CLOCK_STALE')
                rows = value['data']
                evidence['events'] = len(rows)
                for row in rows:
                    if not isinstance(row, dict) or row.get('annType') != category:
                        raise _Failure('BITGET_CATEGORY_INVALID')
                    identity, publication = row.get('annId'), row.get('cTime')
                    if not isinstance(identity, str) or not identity.isascii() or not identity.isdigit() or len(identity) > 40 or identity in seen:
                        raise _Failure('BITGET_PAGINATION_INVALID')
                    if cursor is not None and int(identity) >= int(cursor):
                        raise _Failure('BITGET_PAGINATION_INVALID')
                    if not isinstance(publication, str) or not publication.isascii() or not publication.isdigit() or not start <= int(publication) <= end:
                        raise _Failure('BITGET_PUBLICATION_INVALID')
                    link = row.get('annUrl')
                    parts = urlsplit(link if isinstance(link, str) else '')
                    if parts.scheme != 'https' or parts.netloc != 'www.bitget.com' or not parts.path.startswith('/en/support/articles/') or parts.username or parts.fragment:
                        raise _Failure('BITGET_LINK_INVALID')
                    title = _plain(row.get('annTitle'))
                    event_id = f'bitget:{category}:{identity}'
                    seen.add(identity)
                    events.append(Event(event_id=event_id, instruments=CRYPTO, impact='HIGH',
                        scheduled_at=datetime.fromtimestamp(int(publication) / 1000, UTC), known_at=checked))
                    labels[event_id] = {'title': title, 'url': link}
                if len(rows) < 10:
                    break
                next_cursor = rows[-1]['annId']
                if next_cursor == cursor:
                    raise _Failure('BITGET_PAGINATION_INVALID')
                cursor = next_cursor
            else:
                raise _Failure('BITGET_PAGINATION_INCOMPLETE')
        return events, labels

    def refresh(self, now):
        now = aware(now)
        if not self._refresh_lock.acquire(blocking=False):
            return
        started, feeds = time.monotonic(), []
        try:
            with self._lock:
                if self._closed:
                    return
                old = self._state
            events, labels, uncertain, failures = [], {}, [], []
            calendar_scope = _month(now, NY)
            if self.calendar == 'nyfed':
                try:
                    rows, names, unknown, calendar_scope = self._nyfed(now, started, feeds)
                    events.extend(rows); labels.update(names); uncertain.extend(unknown)
                except Exception as error:
                    code = _error_code(error); failures.append(code)
                    if feeds:
                        feeds[-1]['error'] = feeds[-1].get('error') or code
            for collector in ((self._bea,) if self.calendar == 'nyfed' else (self._bls, self._bea)):
                begin = len(feeds)
                try:
                    rows, names = collector(now, started, feeds)
                    events.extend(rows); labels.update(names)
                except Exception as error:
                    code = _error_code(error); failures.append(code)
                    if len(feeds) > begin and not any(row.get('error') for row in feeds[begin:]):
                        feeds[-1]['error'] = feeds[-1].get('error') or code
            fed_scope = _month(now, NY)
            try:
                fed, names, unknown, fed_scope = self._fed(now, started, feeds)
                events.extend(fed); labels.update(names); uncertain.extend(unknown)
            except Exception as error:
                code = _error_code(error); failures.append(code)
                if feeds:
                    feeds[-1]['error'] = feeds[-1].get('error') or code
            vendor, names, lookback = self._announcements(now, started, feeds, failures)
            events.extend(vendor); labels.update(names)
            if failures:
                raise _Failure(failures[0])
            observed = self._stamp(now, started)
            year = now.astimezone(NY).year
            begin = max(lookback, calendar_scope[0], fed_scope[0], _local(datetime(year, 1, 1), NY))
            end = min(calendar_scope[1], fed_scope[1], _local(datetime(year + 1, 1, 1), NY))
            if not begin <= now < end:
                raise _Failure('CALENDAR_COVERAGE_INCOMPLETE')
            news = News(source=self.source, observed_at=observed, coverage_start=begin, coverage_end=end, events=tuple(events))
            snapshot = {'news': news.model_dump(mode='json'), 'labels': labels,
                'uncertain': [{**row, 'start': row['start'].isoformat(), 'end': row['end'].isoformat()} for row in uncertain]}
            value = {'version': self.version, 'state': 'READY', 'error': None, 'snapshot': snapshot, 'feeds': feeds}
            with self._lock:
                if self._closed:
                    return
            self._persist(value)  # No REST/read lock is held while SQLite writes.
            with self._lock:
                if not self._closed:
                    self._state = value
        except Exception as error:
            code = _error_code(error)
            with self._lock:
                if self._closed:
                    return
                old = self._state
                missing = [{**_feed(name, url), 'error': 'SOURCE_NOT_ACQUIRED'} for name, url in self.feeds if not any(row['name'] == name for row in feeds)]
                value = {**old, 'state': 'UNAVAILABLE', 'error': code, 'feeds': feeds + missing if feeds else old['feeds']}
                self._state = value  # A known failure blocks grants before slow persistence.
            try:
                self._persist(value)
            except Exception:
                with self._lock:
                    if not self._closed:
                        self._state = {**value, 'error': 'NEWS_PERSISTENCE_ERROR'}
        finally:
            self._refresh_lock.release()

    def _view(self, now):
        now = aware(now)
        with self._lock:
            value, closed = self._state, self._closed
        snapshot = value.get('snapshot')
        if closed:
            return value, None, 'UNAVAILABLE', 'COLLECTOR_CLOSED'
        if value['state'] != 'READY' or snapshot is None:
            return value, None, value['state'], value['error']
        news = News.model_validate(snapshot['news'])
        age = (now - news.observed_at).total_seconds()
        if age < 0:
            return value, None, 'UNAVAILABLE', 'NEWS_FROM_FUTURE'
        if age > TTL:
            return value, None, 'STALE', 'NEWS_STALE'
        for feed in value['feeds']:
            checked = datetime.fromisoformat(feed['checked_at']) if feed.get('checked_at') else None
            if checked is None or not 0 <= (now - checked).total_seconds() <= TTL:
                return value, None, 'STALE', 'NEWS_COMPONENT_STALE'
            try:
                _http_metadata(feed['upstream_date'], feed.get('age'), feed.get('last_modified'), now, fetched_at=checked)
            except _Failure:
                return value, None, 'STALE', 'NEWS_COMPONENT_STALE'
        start, end = news.coverage_start, news.coverage_end
        for row in snapshot['uncertain']:
            first, last = datetime.fromisoformat(row['start']), datetime.fromisoformat(row['end'])
            if first <= now < last:
                return value, None, 'UNAVAILABLE', 'CALENDAR_TIME_AMBIGUOUS'
            if last <= now:
                start = max(start, last)
            elif first > now:
                end = min(end, first)
        if not start <= now < end:
            return value, None, 'UNAVAILABLE', 'CALENDAR_COVERAGE_INCOMPLETE'
        return value, news.model_copy(update={'coverage_start': start, 'coverage_end': end}), 'READY', None

    def read(self, instrument, now):
        if instrument not in INSTRUMENTS:
            return None
        return self._view(now)[1]

    def status(self, now):
        now = aware(now)
        value, news, state, error = self._view(now)
        snapshot = value.get('snapshot')
        recorded = News.model_validate(snapshot['news']) if snapshot else None
        upcoming = []
        if recorded:
            for event in sorted(recorded.events, key=lambda row: (row.scheduled_at, row.event_id)):
                if event.scheduled_at >= now:
                    label = snapshot['labels'][event.event_id]
                    upcoming.append({'event_id': event.event_id, 'title': label['title'], 'scheduled_at': event.scheduled_at.isoformat(),
                                     'url': label['url'], 'instruments': list(event.instruments)})
                if len(upcoming) == 10:
                    break
        coverage = news or recorded
        attribution = (f'Economic Indicators Calendar, © {(recorded.observed_at if recorded else now).astimezone(NY).year} Federal Reserve Bank of New York'
                       if self.calendar == 'nyfed' else None)
        return {'source': self.source, 'state': state, 'error': error, 'scope': self.scope,
            **({'attribution': attribution} if attribution else {}),
            'observed_at': recorded.observed_at.isoformat() if recorded else None,
            'coverage_start': coverage.coverage_start.isoformat() if coverage else None,
            'coverage_end': coverage.coverage_end.isoformat() if coverage else None,
            'feeds': [dict(row) for row in value['feeds']], 'next_high': upcoming,
            'uncertain_windows': snapshot['uncertain'] if snapshot else []}

    def close(self):
        with self._lock:
            self._closed = True
        if self._client is not None:
            self._client.close()
