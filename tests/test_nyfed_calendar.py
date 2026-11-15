"""Entirely synthetic NY Fed-shaped HTML; no real news or trading injection."""
import calendar
from datetime import datetime, timedelta, timezone
from html import escape

import pytest

from core.paper.nyfed_calendar import parse_nyfed, _utc


NOW = datetime(2026, 11, 15, 22, tzinfo=timezone.utc)
CORE = (
    ('Consumer Price Index', 'https://www.bls.gov/news.release/cpi.toc.htm'),
    ('Employment Situation', 'https://www.bls.gov/news.release/empsit.toc.htm'),
    ('Producer Price Index (PPI)', 'https://www.bls.gov/pPI/'),
    ('JOLTS', 'https://www.bls.gov/news.release/jolts.toc.htm'),
)


def event(title, url, clock='08:30'):
    return f'<a href="{escape(url, quote=True)}">{escape(title)}</a><img src="/pdf.gif"><br/>({clock})<br/><br/>'


def synthetic_calendar(year=2026, month=11, *, extra=(), omit_core=None, omit_days=(), duplicate_day=None):
    first = datetime(year, month, 1)
    previous = first - timedelta(days=1)
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    days = [day for day in range(1, calendar.monthrange(year, month)[1] + 1) if first.replace(day=day).weekday() < 5]
    events = {day: [] for day in days}
    for day, (title, url) in zip(days, CORE):
        if title != omit_core:
            events[day].append(event(title, url))
    events[days[-1]].extend(event(*row) for row in extra)
    rows = []
    for week in calendar.monthcalendar(year, month):
        if not any(week[:5]):
            continue
        cells = ['<td>&nbsp;</td>']
        for day in week[:5]:
            if day in omit_days:
                continue
            value = duplicate_day if duplicate_day and day == days[1] else day
            cells.append('<td>' + (f'<div>{value:02d}<br/><span class="ts-accordion-content">' + ''.join(events[day]) + '</span></div>' if day else '') + '</td>')
        cells.append('<td>&nbsp;</td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')
    month_link = lambda value: f'/research/calendars/i-{calendar.month_abbr[value.month].lower()}{value.year % 100:02d}.html'
    return ('<p>Provides dates (all Eastern Time).</p>'
            f'<a href="{month_link(previous)}">PREVIOUS MONTH</a>'
            f'<td class="ts-data-table-head"><div>{calendar.month_name[month]} {year}</div></td>'
            f'<a href="{month_link(following)}">NEXT MONTH</a>'
            '<table class="research-table-1col greyborder"><tr><td></td>'
            + ''.join(f'<td>{name}</td>' for name in list(calendar.day_name)[:5]) + '<td></td></tr>'
            + ''.join(rows) + '</table>'
            '<p>Dates and times are tentative and subject to immediate change.</p>')


def parse(text=None, now=NOW, checked=None):
    return parse_nyfed(text or synthetic_calendar(), now, checked or now)


def test_complete_synthetic_month_converts_eastern_preserves_scope_and_observation():
    checked = NOW + timedelta(seconds=2)
    events, labels, uncertain, coverage = parse(checked=checked)
    assert len(events) == 4 and not uncertain
    assert coverage == (datetime(2026, 11, 1, 4, tzinfo=timezone.utc), datetime(2026, 12, 1, 5, tzinfo=timezone.utc))
    assert events[0].scheduled_at == datetime(2026, 11, 2, 13, 30, tzinfo=timezone.utc)
    assert all(row.known_at == checked and row.instruments == ('*',) and row.impact == 'HIGH' for row in events)
    assert {(row['title'], row['url']) for row in labels.values()} == set(CORE)
    assert all(row.event_id.startswith('nyfed:') for row in events)


def test_id_stable_on_revalidation_and_clock_change_but_known_at_not_fabricated():
    first = parse()[0]
    later = parse(synthetic_calendar().replace('(08:30)', '(08:31)', 1), checked=NOW + timedelta(seconds=5))[0]
    assert [row.event_id for row in first] == [row.event_id for row in later]
    assert first[0].scheduled_at != later[0].scheduled_at
    assert later[0].known_at == NOW + timedelta(seconds=5)


def test_current_month_comes_from_new_york_not_utc_date():
    now = datetime(2026, 11, 1, 3, tzinfo=timezone.utc)
    events, _, _, coverage = parse(synthetic_calendar(2026, 10), now=now)
    assert coverage[0] == datetime(2026, 10, 1, 4, tzinfo=timezone.utc)
    assert events


@pytest.mark.parametrize('month,offset', [(1, 5), (7, 4), (12, 5)])
def test_month_navigation_year_rollover_and_dst_seasons(month, offset):
    now = datetime(2026, month, 15, 20, tzinfo=timezone.utc)
    _, _, _, coverage = parse(synthetic_calendar(2026, month), now=now)
    assert coverage[0] == datetime(2026, month, 1, offset, tzinfo=timezone.utc)


@pytest.mark.parametrize('clock', ['02:00', '1:30', '07:59'])
def test_bare_morning_afternoon_ambiguous_time_becomes_full_day_risk(clock):
    events, labels, uncertain, _ = parse(synthetic_calendar(extra=[('Synthetic ambiguous release', '/research/policy/rstar', clock)]))
    assert len(events) == len(labels) == 4
    assert len(uncertain) == 1
    assert uncertain[0]['start'] == datetime(2026, 11, 30, 5, tzinfo=timezone.utc)
    assert uncertain[0]['end'] == datetime(2026, 12, 1, 5, tzinfo=timezone.utc)
    assert uncertain[0]['reason'] == 'NYFED_AMPM_UNSPECIFIED'


@pytest.mark.parametrize('clock,hour', [('2:00 p.m.', 19), ('2:00 am', 7), ('14:00', 19), ('12:45', 17)])
def test_explicit_or_unambiguous_clock(clock, hour):
    events, _, uncertain, _ = parse(synthetic_calendar(extra=[('Synthetic timed release', '/research/test', clock)]))
    assert not uncertain
    assert events[-1].scheduled_at.hour == hour


@pytest.mark.parametrize('title', [row[0] for row in CORE])
def test_missing_required_semantic_release_is_never_an_empty_permission(title):
    with pytest.raises(ValueError, match='NYFED_CORE_EVENTS_MISSING'):
        parse(synthetic_calendar(omit_core=title))


def test_correct_title_with_wrong_source_does_not_satisfy_semantic_requirement():
    with pytest.raises(ValueError, match='NYFED_CORE_EVENTS_MISSING'):
        parse(synthetic_calendar().replace(CORE[0][1], 'https://example.org/not-official'))


@pytest.mark.parametrize('mutate', [
    lambda text: text.replace('November 2026', 'October 2026'),
    lambda text: text.replace('all Eastern Time', 'timezone unspecified'),
    lambda text: text.replace('Dates and times are tentative and subject to immediate change.', ''),
    lambda text: text.replace('/research/calendars/i-dec26.html', '/research/calendars/i-jan27.html'),
    lambda text: text.replace('NEXT MONTH', 'NEXT WEEK'),
    lambda text: text.replace('Monday', 'Sunday'),
    lambda text: text.replace('</table>', ''),
    lambda text: text.replace('</span>', '', 1),
    lambda text: text.replace('(08:30)', '', 1),
    lambda text: text.replace('(08:30)', '(08:30) unparsed event', 1),
    lambda text: text.replace('(08:30)', '(TBD)', 1),
    lambda text: text.replace('>02<br/>', '>31<br/>', 1),
    lambda text: text.replace('>02<br/>', '>03<br/>', 1),
])
def test_changed_or_incomplete_format_fails_closed(mutate):
    with pytest.raises(ValueError):
        parse(mutate(synthetic_calendar()))


def test_missing_whole_workday_cannot_silently_reduce_coverage():
    with pytest.raises(ValueError):
        parse(synthetic_calendar(omit_days=[11]))


def test_duplicate_event_rejected_instead_of_silently_deduplicated():
    text = synthetic_calendar()
    one = event(*CORE[0])
    with pytest.raises(ValueError, match='NYFED_EVENT_DUPLICATE'):
        parse(text.replace(one, one + one))


def test_empty_calendar_fails_closed_even_with_complete_date_grid():
    text = synthetic_calendar()
    for row in CORE:
        text = text.replace(event(*row), '')
    with pytest.raises(ValueError, match='NYFED_CALENDAR_EMPTY_OR_OVERSIZED'):
        parse(text)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://example.org/report', 'https://user:password@example.org/report', 'https://example.org:444/report'])
def test_unsafe_event_links_rejected(url):
    with pytest.raises(ValueError, match='NYFED_SOURCE_URL_INVALID'):
        parse(synthetic_calendar(extra=[('Synthetic unsafe release', url, '10:00')]))


@pytest.mark.parametrize('clock', ['24:00', '08:60', '13:00 pm', '00:00 am'])
def test_invalid_time_fields_rejected(clock):
    with pytest.raises(ValueError, match='NYFED_EVENT_TIME_INVALID'):
        parse(synthetic_calendar(extra=[('Synthetic invalid time', '/research/test', clock)]))


@pytest.mark.parametrize('stamp', [datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)])
def test_nonexistent_or_ambiguous_local_dst_instant_is_rejected(stamp):
    with pytest.raises(ValueError, match='NYFED_LOCAL_TIME_AMBIGUOUS'):
        _utc(stamp)


def test_naive_clock_and_inflight_month_rollover_are_rejected():
    with pytest.raises(ValueError, match='NYFED_AWARE_CLOCK_REQUIRED'):
        parse(now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match='NYFED_MONTH_BOUNDARY_CHANGED'):
        parse(now=datetime(2026, 12, 1, 4, 59, tzinfo=timezone.utc), checked=datetime(2026, 12, 1, 5, tzinfo=timezone.utc))


def test_fake_scope_inside_script_does_not_grant_timezone_permission():
    text = synthetic_calendar().replace('all Eastern Time', 'unknown timezone')
    with pytest.raises(ValueError, match='NYFED_TIME_SCOPE_MISSING'):
        parse('<script>all Eastern Time</script>' + text)


def test_stray_div_close_only_in_empty_out_of_month_cell_is_harmless():
    now = datetime(2026, 12, 15, 22, tzinfo=timezone.utc)
    text = synthetic_calendar(2026, 12).replace('<td></td>', '<td>\n</div>\n</td>')
    assert len(parse(text, now=now)[0]) == 4
    with pytest.raises(ValueError, match='NYFED_CALENDAR_STRUCTURE_INVALID'):
        parse(text.replace('<td>\n</div>', '<td>unparsed content\n</div>'), now=now)
