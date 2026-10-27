from copy import deepcopy
from datetime import datetime, timezone

import pytest

from core.paper.directional_market import BITGET_SOURCE, parse_book, source_for

STAMP = 1793088605181


def data():
    return {'code': '00000', 'data': {'asks': [['79866.00', '0.375526']],
            'bids': [['79865.99', '0.406126']], 'ts': str(STAMP)}}


def test_public_top_of_book_exact_values_and_source_timestamp():
    row = parse_book('BTCUSDT', data(), STAMP+20)
    assert str(row.bid) == '79865.99' and str(row.ask) == '79866.00'
    assert row.source == BITGET_SOURCE and row.at.microsecond == 181000
    assert row.at == datetime(2026, 10, 27, 8, 10, 5, 181000, timezone.utc)


@pytest.mark.parametrize('change', [
    lambda value: value.update(code='ERROR'),
    lambda value: value['data'].update(ts=True),
    lambda value: value['data'].update(ts=str(STAMP+1)),
    lambda value: value['data'].update(ts=str(STAMP-60001)),
    lambda value: value['data'].update(bids=[]),
    lambda value: value['data'].update(asks=[['79866', '0']]),
    lambda value: value['data'].update(bids=[['79867', '1']]),
    lambda value: value['data'].update(bids=[[79865.99, '1']]),
    lambda value: value['data'].update(bids=[['NaN', '1']]),
    lambda value: value['data'].update(bids=[['79865', 'NaN']]),
])
def test_bad_quotes_do_not_become_executable(change):
    payload = deepcopy(data())
    change(payload)
    with pytest.raises(ValueError):
        parse_book('BTCUSDT', payload, STAMP)


def test_unknown_products_do_not_silently_substitute_gold_tokens():
    assert 'swissquote' in source_for('XAU/USD')
    for value in ('XAUUSDT', 'PAXGUSDT', 'GOLD', 'BTC/USD'):
        with pytest.raises(ValueError):
            source_for(value)
