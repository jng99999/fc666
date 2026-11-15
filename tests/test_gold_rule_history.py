"""Synthetic raw ASCII only; no network or source-month download in unit tests."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import zipfile

import pytest

from core.paper.price_action_rules import RuleBar
from scripts.freeze_gold_rule_history import parse_ticks, decode_archive, dataset_sha, normalized_bars_sha, SOURCE


def tick(minute=0, second=0, bid='2000', ask='2001', day='20250102', hour=0):
    return f'{day} {hour:02}{minute:02}{second:02}000,{bid},{ask},0\n'.encode('ascii')


def complete(minute=0):
    return [tick(minute, 0), tick(minute, 20, '2001', '2002'), tick(minute, 40), tick(minute, 59)]


def test_paired_mid_ohlc_fixed_est_to_utc_and_provenance_hash_are_exact():
    rows = complete()
    bars, quality = parse_ticks(rows)
    assert len(bars) == 1
    value = RuleBar.model_validate(bars[0])
    assert value.source == SOURCE and value.volume is None
    assert value.open_time == datetime(2025, 1, 2, 5, tzinfo=timezone.utc)
    assert value.open == value.close == Decimal('2000.5')
    assert value.high == Decimal('2001.5') and value.low == Decimal('2000.5')
    assert quality['raw_csv_sha256'] == hashlib.sha256(b''.join(rows)).hexdigest()
    assert quality['raw_csv_bytes'] == len(b''.join(rows))


@pytest.mark.parametrize('seconds,expected', [([20, 40], 1), ([21, 41], 0), ([0, 20, 39], 0),
                                          ([0, 30, 59], 1), ([0, 31, 59], 0)])
def test_coverage_boundary_20_seconds_and_internal_gap_30_seconds(seconds, expected):
    bars, _ = parse_ticks([tick(second=second) for second in seconds])
    assert len(bars) == expected


def test_identical_duplicates_do_not_add_quotes_and_conflicts_quarantine_the_minute():
    rows = complete()
    bars, quality = parse_ticks([rows[0], rows[0], *rows[1:]])
    assert len(bars) == 1 and quality['identical_duplicates'] == 1
    bars, quality = parse_ticks([rows[0], tick(bid='1999'), *rows[1:], *complete(1)])
    assert len(bars) == 1 and bars[0]['open_time'].endswith('05:01:00+00:00')
    assert quality['conflicting_timestamp_events'] == 1 and quality['quarantined_minutes'] == 1


def test_clock_backwards_quarantines_already_seen_interval_without_repair_or_filling():
    bars, quality = parse_ticks([*complete(), *complete(1), tick(0, 50), *complete(2)])
    assert len(bars) == 1 and bars[0]['open_time'].endswith('05:02:00+00:00')
    assert quality['backwards_tick_events'] == 1 and quality['quarantined_minutes'] == 2


def test_absent_minutes_remain_absent_and_zero_volume_is_not_invented():
    bars, quality = parse_ticks([*complete(), *complete(2)])
    assert len(bars) == 2 and quality['complete_minutes'] == 2
    assert bars[0]['close_time'] != bars[1]['open_time']
    assert all(bar['volume'] is None for bar in bars)


@pytest.mark.parametrize('row', [b'20250102 000000000,NaN,2001,0\n', b'20250102 000000000,2002,2001,0\n',
                               b'20250202 000000000,2000,2001,0\n', b'20250102 006000000,2000,2001,0\n',
                               b'20250102 000000000,2000,2001\n'])
def test_malformed_crossed_nonfinite_or_other_month_tick_fails_closed(row):
    with pytest.raises(ValueError):
        parse_ticks([row])


def test_archive_requires_exact_tick_member_and_decodes_without_extracting(tmp_path):
    path = tmp_path / 'test.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('DAT_ASCII_XAUUSD_T_202501.csv', b''.join(complete()))
    assert len(decode_archive(path)[0]) == 1
    wrong = tmp_path / 'wrong.zip'
    with zipfile.ZipFile(wrong, 'w') as archive:
        archive.writestr('DAT_ASCII_XAUUSD_M1_202501.csv', b''.join(complete()))
    with pytest.raises(ValueError):
        decode_archive(wrong)


def test_mid_uses_explicit_decimal_precision_and_never_rounds_the_source_quote():
    rows = [tick(second=second, bid='123456789012345678.123456789123',
                 ask='123456789012345678.123456789124') for second in (0, 20, 40, 59)]
    bars, _ = parse_ticks(rows)
    assert bars[0]['open'] == '123456789012345678.1234567891235'


def test_dataset_identity_depends_on_prices_and_scope_not_variable_download_time():
    bars, _ = parse_ticks(complete())
    data = {'instrument': 'XAU/USD', 'source': SOURCE, 'timeframe': '1m', 'bars': bars}
    assert dataset_sha({**data, 'provenance': {'download_seconds': 1}}) == dataset_sha({**data, 'provenance': {'download_seconds': 20}})
    assert dataset_sha(data) != dataset_sha({**data, 'source': 'different'})


def test_runner_bar_hash_uses_normalized_rulebar_time_and_preserves_source():
    bars, _ = parse_ticks(complete())
    different_offset = [{**bars[0], 'open_time': '2025-01-02T13:00:00+08:00',
                         'close_time': '2025-01-02T13:01:00+08:00'}]
    assert normalized_bars_sha(bars) == normalized_bars_sha(different_offset)
    assert normalized_bars_sha(bars) != normalized_bars_sha([{**bars[0], 'source': 'other'}])
