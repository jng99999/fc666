"""Parse the NY Fed's explicitly scoped key-indicators HTML calendar.

This is a calendar parser, not a claim that NY Fed lists every BLS release.
No network calls, event-name trading rules or fabricated publication dates.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from core.paper.directional import Event


NY = ZoneInfo('America/New_York')
ORIGIN = 'https://www.newyorkfed.org'
_VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'))
# Scope sentinels, not guessed dates or title-based influence classification.
# A legitimate month missing any sentinel remains unavailable conservatively.
_REQUIRED = frozenset((
    ('Consumer Price Index', 'https://www.bls.gov/news.release/cpi.toc.htm'),
    ('Employment Situation', 'https://www.bls.gov/news.release/empsit.toc.htm'),
    ('Producer Price Index (PPI)', 'https://www.bls.gov/pPI/'),
    ('JOLTS', 'https://www.bls.gov/news.release/jolts.toc.htm'),
))


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None]
    children: list[_Node | str] = field(default_factory=list)


def _text(node: _Node) -> str:
    return ' '.join(''.join(child if isinstance(child, str) else ' ' + _text(child) + ' '
                            for child in node.children).split())


class _CalendarHTML(HTMLParser):
    """Capture one known calendar table without executing or evaluating HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.table: _Node | None = None
        self.stack: list[_Node] = []
        self.closed = False
        self.visible: list[str] = []
        self.headers: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.header: list[str] | None = None
        self.anchor: tuple[str, list[str]] | None = None
        self.hidden = 0

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        classes = (attrs.get('class') or '').split()
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag == 'td' and 'ts-data-table-head' in classes:
            if self.header is not None:
                raise ValueError('NYFED_MONTH_HEADER_INVALID')
            self.header = []
        if tag == 'a':
            if self.anchor is not None:
                raise ValueError('NYFED_LINK_INVALID')
            self.anchor = (attrs.get('href') or '', [])
        if tag == 'table' and {'research-table-1col', 'greyborder'} <= set(classes):
            if self.table is not None:
                raise ValueError('NYFED_CALENDAR_MULTIPLE')
            self.table = _Node(tag, attrs)
            self.stack = [self.table]
        elif self.stack:
            if tag == 'table' or tag in ('script', 'style'):
                raise ValueError('NYFED_CALENDAR_STRUCTURE_INVALID')
            node = _Node(tag, attrs)
            self.stack[-1].children.append(node)
            if tag not in _VOID:
                self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if tag == 'a' and self.anchor is not None:
            href, parts = self.anchor
            self.links.append((href, ' '.join(''.join(parts).split())))
            self.anchor = None
        if tag == 'td' and self.header is not None:
            self.headers.append(' '.join(''.join(self.header).split()))
            self.header = None
        if self.stack and tag not in _VOID:
            if self.stack[-1].tag != tag:
                # The actual December 2026 HTML has a stray </div> in its
                # empty, out-of-month Monday cell. HTML browsers ignore it;
                # permit only this empty-cell case, never discard event text.
                if tag == 'div' and self.stack[-1].tag == 'td' and not self.stack[-1].children:
                    return
                if (tag == 'div' and self.stack[-1].tag == 'td'
                        and all(isinstance(part, str) and not part.strip() for part in self.stack[-1].children)):
                    return
                raise ValueError('NYFED_CALENDAR_STRUCTURE_INVALID')
            self.stack.pop()
            if not self.stack:
                self.closed = True

    def handle_data(self, data):
        if self.hidden:
            return
        self.visible.append(data)
        if self.header is not None:
            self.header.append(data)
        if self.anchor is not None:
            self.anchor[1].append(data)
        if self.stack:
            self.stack[-1].children.append(data)


def _utc(local: datetime) -> datetime:
    """Reject both nonexistent and ambiguous New York local instants."""
    candidates = set()
    for fold in (0, 1):
        aware = local.replace(tzinfo=NY, fold=fold)
        stamp = aware.astimezone(timezone.utc)
        if stamp.astimezone(NY).replace(tzinfo=None) == local:
            candidates.add(stamp)
    if len(candidates) != 1:
        raise ValueError('NYFED_LOCAL_TIME_AMBIGUOUS')
    return candidates.pop()


def _aware(stamp: datetime) -> datetime:
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('NYFED_AWARE_CLOCK_REQUIRED')
    return stamp.astimezone(timezone.utc)


def _parts(node: _Node):
    """Keep event anchors distinct from each immediately following time."""
    if node.tag == 'a':
        yield node
    elif node.tag == 'br':
        yield ' '
    elif node.tag != 'img':
        for child in node.children:
            if isinstance(child, str):
                yield child
            else:
                yield from _parts(child)


def _url(value: str) -> str:
    if not value or any(ord(char) < 33 for char in value):
        raise ValueError('NYFED_SOURCE_URL_INVALID')
    result = urljoin(ORIGIN, value)
    try:
        parts = urlsplit(result)
        valid = (parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
                 and parts.port in (None, 443))
    except ValueError as error:
        raise ValueError('NYFED_SOURCE_URL_INVALID') from error
    if not valid:
        raise ValueError('NYFED_SOURCE_URL_INVALID')
    return result


def parse_nyfed(text: str, now: datetime, checked: datetime):
    """Return events, labels, unknown-time windows and the verified NY month.

    Bare 01:00–07:59 clocks cannot distinguish morning from afternoon (the
    actual calendar prints R-Star 02:00 although its release page says 2 p.m.).
    Preserve those as full-day risk instead of guessing a point timestamp.
    """
    now, checked = _aware(now), _aware(checked)
    local = now.astimezone(NY)
    if (local.year, local.month) != (checked.astimezone(NY).year, checked.astimezone(NY).month):
        raise ValueError('NYFED_MONTH_BOUNDARY_CHANGED')
    if not isinstance(text, str) or not text or len(text) > 1500000:
        raise ValueError('NYFED_CALENDAR_INVALID')
    parsed = _CalendarHTML()
    parsed.feed(text)
    parsed.close()
    if parsed.table is None or not parsed.closed or parsed.stack:
        raise ValueError('NYFED_CALENDAR_INCOMPLETE')
    if parsed.headers != [f'{calendar.month_name[local.month]} {local.year}']:
        raise ValueError('NYFED_MONTH_COVERAGE_MISSING')
    visible = ' '.join(' '.join(parsed.visible).split())
    if 'all Eastern Time' not in visible or 'Dates and times are tentative and subject to immediate change.' not in visible:
        raise ValueError('NYFED_TIME_SCOPE_MISSING')
    first = datetime(local.year, local.month, 1)
    next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    previous_month = first - timedelta(days=1)
    for title, month in (('PREVIOUS MONTH', previous_month), ('NEXT MONTH', next_month)):
        expected = f'/research/calendars/i-{calendar.month_abbr[month.month].lower()}{month.year % 100:02d}.html'
        matching = [href for href, label in parsed.links if label == title]
        if len(matching) != 1 or _url(matching[0]) != ORIGIN + expected:
            raise ValueError('NYFED_MONTH_NAVIGATION_INVALID')
    rows = [child for child in parsed.table.children if isinstance(child, _Node) and child.tag == 'tr']
    if not rows:
        raise ValueError('NYFED_CALENDAR_INCOMPLETE')
    header = [_text(child) for child in rows[0].children if isinstance(child, _Node) and child.tag == 'td']
    if [part for part in header if part] != list(calendar.day_name)[:5]:
        raise ValueError('NYFED_WEEKDAY_HEADER_INVALID')
    days, events, labels, uncertain, seen = [], [], {}, [], set()
    for row in rows[1:]:
        cells = [child for child in row.children if isinstance(child, _Node) and child.tag == 'td']
        if not 2 <= len(cells) <= 7:
            raise ValueError('NYFED_CALENDAR_ROW_INVALID')
        for column, cell in enumerate(cells):
            if not _text(cell):
                continue
            if not 1 <= column <= 5:
                raise ValueError('NYFED_CALENDAR_COLUMN_INVALID')
            parts = list(_parts(cell))
            prefix = ''.join(part for part in parts[:next((i for i, part in enumerate(parts) if isinstance(part, _Node)), len(parts))])
            if not re.fullmatch(r'\s*\d{1,2}\s*', prefix):
                raise ValueError('NYFED_DATE_INVALID')
            try:
                day = first.replace(day=int(prefix))
            except ValueError as error:
                raise ValueError('NYFED_DATE_INVALID') from error
            if day.weekday() != column - 1:
                raise ValueError('NYFED_WEEKDAY_MISMATCH')
            days.append(day.day)
            anchors = [i for i, part in enumerate(parts) if isinstance(part, _Node)]
            for index, start in enumerate(anchors):
                anchor = parts[start]
                end = anchors[index + 1] if index + 1 < len(anchors) else len(parts)
                tail = ''.join(parts[start + 1:end]).strip()
                clock = re.fullmatch(r'\(\s*(\d{1,2}):(\d{2})(?:\s*(a\.?m\.?|p\.?m\.?))?\s*\)', tail, re.I)
                if clock is None:
                    raise ValueError('NYFED_EVENT_TIME_INVALID')
                hour, minute = int(clock[1]), int(clock[2])
                marker = clock[3]
                if hour > 23 or minute > 59 or (marker and not 1 <= hour <= 12):
                    raise ValueError('NYFED_EVENT_TIME_INVALID')
                title, url = _text(anchor), _url(anchor.attrs.get('href') or '')
                if not title or len(title) > 1000:
                    raise ValueError('NYFED_EVENT_TITLE_INVALID')
                identity = 'nyfed:' + sha256(json.dumps([day.date().isoformat(), title, url], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
                if identity in seen:
                    raise ValueError('NYFED_EVENT_DUPLICATE')
                seen.add(identity)
                if not marker and 1 <= hour <= 7:
                    uncertain.append({'start': _utc(day), 'end': _utc(day + timedelta(days=1)),
                                      'title': title, 'url': url, 'reason': 'NYFED_AMPM_UNSPECIFIED'})
                else:
                    if marker:
                        hour = hour % 12 + (12 if marker.lower().startswith('p') else 0)
                    stamp = _utc(day.replace(hour=hour, minute=minute))
                    events.append(Event(event_id=identity, instruments=('*',), impact='HIGH', scheduled_at=stamp, known_at=checked))
                    labels[identity] = {'title': title, 'url': url}
    expected_days = [day for day in range(1, calendar.monthrange(local.year, local.month)[1] + 1)
                     if first.replace(day=day).weekday() < 5]
    if days != expected_days:
        raise ValueError('NYFED_MONTH_DAYS_INCOMPLETE')
    if not events or len(events) + len(uncertain) > 1000:
        raise ValueError('NYFED_CALENDAR_EMPTY_OR_OVERSIZED')
    if not _REQUIRED <= {(row['title'], row['url']) for row in labels.values()}:
        raise ValueError('NYFED_CORE_EVENTS_MISSING')
    return events, labels, uncertain, (_utc(first), _utc(next_month))
