"""Synthetic protocol fixtures; real HTTP is separately verified, never fabricated."""
from datetime import datetime, timezone
from decimal import Decimal
import json

import httpx
import pytest

from core.research import market_evidence as market

NOW = 1800000000000


@pytest.fixture(autouse=True)
def isolated_gold_configuration(monkeypatch):
    for name in ('FC666_GOLD_HISTORY_URL', 'FC666_GOLD_HISTORY_SOURCE', 'FC666_GOLD_HISTORY_TOKEN'):
        monkeypatch.delenv(name, raising=False)


def rows(count=100, *, step=1, start=None):
    start = NOW - count * 60000 if start is None else start
    result = []
    for index in range(count):
        close = Decimal(300 if step < 0 else 100) + index * step
        result.append([str(start + index * 60000), str(close), str(close + 1),
                       str(close - 1), str(close), '2', '200', '200'])
    return result


def payload(values=None):
    return {'code': '00000', 'data': rows() if values is None else values}


@pytest.fixture
def mock_http(monkeypatch):
    original = httpx.Client
    requests = []

    def install(handler):
        def factory(**kwargs):
            assert kwargs == {'timeout': 6.0, 'follow_redirects': False, 'trust_env': False}

            def record(request):
                requests.append(request)
                assert max(request.extensions['timeout'].values()) <= 8
                return handler(request)

            return original(transport=httpx.MockTransport(record), **kwargs)

        monkeypatch.setattr(market.httpx, 'Client', factory)
        return requests

    return install


def test_request_is_bounded_fixed_origin_spot_and_original_symbol(mock_http):
    calls = mock_http(lambda _: httpx.Response(200, json=payload(rows(200))))
    result = market.evidence('BTCUSDT', datetime.fromtimestamp(NOW / 1000, timezone.utc))
    assert result['status'] == 'READY'
    assert len(result['bars']) == 200 and result['indicators']['input_bars'] == 200
    assert result['quote_source'] == 'bitget.public:spot:orderbook:step0'
    assert result['candle_source'] == 'bitget.public:spot:candles:1min'
    assert result['currency'] == 'USDT' and result['trading_enabled'] is False
    assert len(calls) == 1 and str(calls[0].url).startswith(market.BITGET_URL + '?')
    assert dict(calls[0].url.params) == {
        'symbol': 'BTCUSDT', 'granularity': '1min', 'limit': '200', 'endTime': str(NOW - 1)}
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize('instrument', ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT',
                                         'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT'])
def test_each_bitget_symbol_remains_exactly_the_requested_instrument(mock_http, instrument):
    calls = mock_http(lambda _: httpx.Response(200, json=payload()))
    result = market.evidence(instrument, NOW)
    assert result['instrument'] == instrument and result['status'] == 'READY'
    assert calls[0].url.params['symbol'] == instrument


@pytest.mark.parametrize('instrument', ['XAU/USD', 'GC=F', 'BTC/USD', 'BTCUSDT_UMCBL',
                                         'https://attacker.example/BTCUSDT', 'BTCUSDT&symbol=ETHUSDT'])
def test_gold_and_unknown_instruments_never_make_substitute_http_requests(mock_http, instrument):
    calls = mock_http(lambda _: pytest.fail('Unsupported market must not make HTTP requests'))
    result = market.evidence(instrument, NOW)
    assert result['status'] == 'UNAVAILABLE' and result['bars'] == []
    assert result['indicators'] is None and not calls
    if instrument == 'XAU/USD':
        assert result['quote_source'] == 'swissquote.public:AT:AT:standard:XAU/USD'
        assert result['reason'] == 'HISTORICAL_CANDLES_UNAVAILABLE'
        assert result['currency'] == 'USD' and result['candle_source'] is None


def test_closed_sorted_unique_candles_discard_unfinished_and_future():
    values = rows()
    normalized = market.parse_candles(payload(values), NOW)
    # Open/future prices deliberately malformed: they cannot affect indicators.
    future = [[str(NOW), 'unfinished', 'x', 'x', 'x', 'x', 'x'],
              [str(NOW + 60000), 'future', 'x', 'x', 'x', 'x', 'x']]
    actual = market.parse_candles(payload(list(reversed(values)) + [values[0]] + future), NOW)
    assert actual == normalized and len(actual) == 100
    assert all(bar['close_time'] <= NOW for bar in actual)
    assert market.calculate_indicators(actual) == market.calculate_indicators(normalized)


def test_identical_numeric_duplicate_formatting_is_accepted():
    values = rows()
    duplicate = list(values[0])
    duplicate[1] = '100.00'
    assert len(market.parse_candles(payload(values + [duplicate]), NOW)) == 100


@pytest.mark.parametrize('mutation', ['conflict', 'gap', 'nan', 'infinity', 'negative_volume',
                                     'zero_price', 'bad_ohlc', 'wrong_interval', 'bool_timestamp',
                                     'huge_number', 'unicode_timestamp'])
def test_bad_closed_history_is_rejected(mutation):
    values = rows()
    if mutation == 'conflict':
        duplicate = list(values[0])
        duplicate[4] = '100.5'
        values.append(duplicate)
    elif mutation == 'gap':
        values.pop(30)
    elif mutation == 'nan':
        values[0][4] = 'NaN'
    elif mutation == 'infinity':
        values[0][2] = 'Infinity'
    elif mutation == 'negative_volume':
        values[0][5] = '-1'
    elif mutation == 'zero_price':
        values[0][3] = '0'
    elif mutation == 'bad_ohlc':
        values[0][2] = '90'
    elif mutation == 'wrong_interval':
        values[0][0] = str(int(values[0][0]) + 1)
    elif mutation == 'bool_timestamp':
        values[0][0] = True
    elif mutation == 'huge_number':
        values[0][2] = '1e999999999'
    else:
        values[0][0] = '١٨٠٠٠٠٠٠٠٠٠٠٠'
    with pytest.raises(ValueError):
        market.parse_candles(payload(values), NOW)


@pytest.mark.parametrize('response', [None, [], {'code': '40000', 'data': []},
                                      {'code': '00000', 'data': {}}, payload(rows(201))])
def test_envelope_and_row_count_are_bounded(response):
    with pytest.raises(ValueError):
        market.parse_candles(response, NOW)


@pytest.mark.parametrize('count', [0, 19, 20, 99])
def test_short_history_never_fabricates_indicators(mock_http, count):
    mock_http(lambda _: httpx.Response(200, json=payload(rows(count))))
    result = market.evidence('BTCUSDT', NOW)
    assert result['status'] == 'INSUFFICIENT_DATA'
    assert len(result['bars']) == count and result['indicators'] is None
    with pytest.raises(ValueError):
        market.calculate_indicators(result['bars'])


def test_stale_history_does_not_look_ready(mock_http):
    mock_http(lambda _: httpx.Response(200, json=payload(rows(start=NOW - 102 * 60000))))
    result = market.evidence('BTCUSDT', NOW)
    assert result['status'] == 'STALE_DATA' and result['indicators'] is None


@pytest.mark.parametrize('step,expected_ema,expected_rsi', [(1, '189.5', '100'),
                                                         (0, '100', '50'), (-1, '210.5', '0')])
def test_analytically_known_trend_and_flat_indicator_results(step, expected_ema, expected_rsi):
    bars = market.parse_candles(payload(rows(step=step)), NOW)
    original = json.dumps(bars)
    indicators = market.calculate_indicators(bars)
    assert Decimal(indicators['ema20']) == Decimal(expected_ema)
    assert Decimal(indicators['rsi14']) == Decimal(expected_rsi)
    assert Decimal(indicators['atr14']) == 2
    assert indicators['as_of'] == NOW and indicators['input_bars'] == 100
    assert json.dumps(bars) == original


def test_wilder_rsi_initial_seed_survives_flat_tail():
    closes = ['44.34', '44.09', '44.15', '43.61', '44.33', '44.83', '45.10', '45.42',
              '45.84', '46.08', '45.89', '46.03', '45.61', '46.28', '46.28']
    closes += [closes[-1]] * 85
    values = rows()
    for value, close in zip(values, closes):
        value[1:5] = [close, str(Decimal(close) + 1), str(Decimal(close) - 1), close]
    indicators = market.calculate_indicators(market.parse_candles(payload(values), NOW))
    assert float(indicators['rsi14']) == pytest.approx(70.46413502109705)


def test_atr_includes_overnight_gap_from_previous_close():
    values = rows(step=0)
    values[1][1:5] = ['110', '111', '109', '110']
    indicators = market.calculate_indicators(market.parse_candles(payload(values), NOW))
    # First two true ranges become 11 instead of 2, then Wilder decays for 85 bars.
    expected = Decimal(2) + Decimal(18) / 14 * (Decimal(13) / 14) ** 85
    assert float(indicators['atr14']) == pytest.approx(float(expected))


@pytest.mark.parametrize('status', [302, 403, 429, 500])
def test_http_failure_no_retry_no_redirect_no_indicators(mock_http, status):
    calls = mock_http(lambda _: httpx.Response(status, headers={'Location': 'https://attacker.example/'}))
    result = market.evidence('BTCUSDT', NOW)
    assert result['status'] == 'UNAVAILABLE' and len(calls) == 1
    assert result['bars'] == [] and result['indicators'] is None


def test_timeout_is_truthfully_unavailable(mock_http):
    def fail(request):
        raise httpx.ReadTimeout('fixture timeout', request=request)
    calls = mock_http(fail)
    result = market.evidence('BTCUSDT', NOW)
    assert result['reason'] == 'CANDLE_HTTP_UNAVAILABLE' and len(calls) == 1


@pytest.mark.parametrize('content', [b'not json', b' ' * (market.MAX_RESPONSE_BYTES + 1),
                                    json.dumps(payload(rows(201))).encode()])
def test_malformed_or_oversized_response_never_creates_indicators(mock_http, content):
    mock_http(lambda _: httpx.Response(200, content=content))
    result = market.evidence('BTCUSDT', NOW)
    assert result['status'] == 'INVALID_DATA' and result['bars'] == []
    assert result['indicators'] is None


@pytest.mark.parametrize('now', [True, -1, 1.5, datetime(2026, 1, 1)])
def test_invalid_or_naive_time_is_rejected_before_http(mock_http, now):
    calls = mock_http(lambda _: pytest.fail('Invalid time must fail before HTTP'))
    with pytest.raises(ValueError):
        market.evidence('BTCUSDT', now)
    assert not calls


def gold_payload(*, null_volume=True):
    bars = market.parse_candles(payload(), NOW)
    for bar in bars:
        del bar['close_time']
        if null_volume:
            bar['volume'] = None
    return {'instrument': 'XAU/USD', 'product_type': 'SPOT',
            'candle_source': 'operator.example:spot:XAU/USD', 'currency': 'USD', 'bars': bars}


def configure_gold(monkeypatch, *, url='https://gold.example.test/history', source='operator.example:spot:XAU/USD',
                   token=''):
    monkeypatch.setenv('FC666_GOLD_HISTORY_URL', url)
    monkeypatch.setenv('FC666_GOLD_HISTORY_SOURCE', source)
    if token:
        monkeypatch.setenv('FC666_GOLD_HISTORY_TOKEN', token)


def test_gold_independent_history_null_volume_and_original_source_times(mock_http, monkeypatch):
    configure_gold(monkeypatch, token='fixture-token')
    response = gold_payload()
    calls = mock_http(lambda _: httpx.Response(200, json=response))
    result = market.evidence('XAU/USD', NOW)
    assert result['status'] == 'READY' and result['configuration_status'] == 'READY'
    assert result['candle_source'] == response['candle_source']
    assert result['quote_source'] == 'swissquote.public:AT:AT:standard:XAU/USD'
    assert result['candle_type'] == 'INDEPENDENT_SPOT_PRICE_HISTORY'
    assert result['product_type'] == 'SPOT' and result['currency'] == 'USD'
    assert result['indicator_scope'] == 'PRICE_ONLY' and result['volume_available'] is False
    assert result['cross_source_basis_verified'] is False
    assert result['price_indicators'] == result['indicators']
    assert Decimal(result['price_indicators']['ema20']) == Decimal('189.5')
    assert [bar['open_time'] for bar in result['bars']] == [bar['open_time'] for bar in response['bars']]
    assert all(bar['volume'] is None for bar in result['bars'])
    assert len(calls) == 1 and str(calls[0].url).startswith('https://gold.example.test/history?')
    assert dict(calls[0].url.params) == {
        'instrument': 'XAU/USD', 'interval': '1m', 'limit': '200', 'end_time': str(NOW)}
    assert calls[0].headers['Authorization'] == 'Bearer fixture-token'
    assert 'fixture-token' not in json.dumps(result)


def test_gold_provider_specific_volume_is_preserved_without_global_claim(mock_http, monkeypatch):
    configure_gold(monkeypatch)
    mock_http(lambda _: httpx.Response(200, json=gold_payload(null_volume=False)))
    result = market.evidence('XAU/USD', NOW)
    assert result['status'] == 'READY' and result['volume_available'] is True
    assert result['volume_scope'] == 'PROVIDER_SPECIFIC_NOT_GLOBAL'
    assert result['bars'][0]['volume'] == '2'


@pytest.mark.parametrize('field,value', [('instrument', 'GC=F'), ('instrument', 'XAUUSDT'),
                                          ('product_type', 'FUTURES'), ('product_type', 'CFD'),
                                          ('currency', 'USDT'), ('currency', 'EUR'),
                                          ('candle_source', 'different-source'), ('candle_source', '')])
def test_gold_rejects_market_currency_and_source_mismatch(mock_http, monkeypatch, field, value):
    configure_gold(monkeypatch)
    response = gold_payload()
    response[field] = value
    mock_http(lambda _: httpx.Response(200, json=response))
    result = market.evidence('XAU/USD', NOW)
    assert result['status'] == 'INVALID_DATA' and result['price_indicators'] is None
    assert result['bars'] == []


@pytest.mark.parametrize('mutation', ['gap', 'conflict', 'future', 'missing_volume', 'bool_timestamp',
                                     'float_timestamp', 'negative_volume', 'bad_prices', 'too_many'])
def test_gold_history_validation(mock_http, monkeypatch, mutation):
    configure_gold(monkeypatch)
    response = gold_payload()
    if mutation == 'gap':
        response['bars'].pop(30)
    elif mutation == 'conflict':
        response['bars'].append({**response['bars'][0], 'close': '100.5'})
    elif mutation == 'future':
        response['bars'].append({**response['bars'][0], 'open_time': NOW})
        response['bars'].append({**response['bars'][0], 'open_time': NOW + 60000})
    elif mutation == 'missing_volume':
        del response['bars'][0]['volume']
    elif mutation == 'bool_timestamp':
        response['bars'][0]['open_time'] = True
    elif mutation == 'float_timestamp':
        response['bars'][0]['open_time'] = float(response['bars'][0]['open_time'])
    elif mutation == 'negative_volume':
        response['bars'][0]['volume'] = '-1'
    elif mutation == 'bad_prices':
        response['bars'][0]['high'] = 'NaN'
    else:
        response['bars'] = response['bars'] * 3
    mock_http(lambda _: httpx.Response(200, json=response))
    result = market.evidence('XAU/USD', NOW)
    if mutation == 'future':
        assert result['status'] == 'READY' and len(result['bars']) == 100
        assert all(bar['close_time'] <= NOW for bar in result['bars'])
    else:
        assert result['status'] == 'INVALID_DATA' and result['price_indicators'] is None


@pytest.mark.parametrize('url,source', [('http://gold.example.test', 'source'),
                                       ('https://user:pass@gold.example.test', 'source'),
                                       ('https://gold.example.test/#fragment', 'source'),
                                       ('https://gold.example.test/#', 'source'),
                                       ('https://gold.example.test:999999/', 'source'),
                                       ('https:///missing-host', 'source'),
                                       ('https://gold.example.test/', ''),
                                       ('https://gold.example.test/', '   '),
                                       ('', 'source')])
def test_gold_invalid_config_fails_before_network(mock_http, monkeypatch, url, source):
    configure_gold(monkeypatch, url=url, source=source)
    calls = mock_http(lambda _: pytest.fail('Invalid config must not reach network'))
    result = market.evidence('XAU/USD', NOW)
    assert result['status'] == 'INVALID_DATA' and result['configuration_status'] == 'INVALID'
    assert result['reason'] == 'GOLD_HISTORY_CONFIG_INVALID' and not calls


@pytest.mark.parametrize('token', ['line\nbreak', ' leading', 'has space', '非ASCII', 'x' * 8193])
def test_gold_invalid_auth_configuration_is_not_sent_or_exposed(mock_http, monkeypatch, token):
    configure_gold(monkeypatch, token=token)
    calls = mock_http(lambda _: pytest.fail('Invalid auth config must not reach network'))
    result = market.evidence('XAU/USD', NOW)
    assert result['configuration_status'] == 'INVALID' and not calls
    assert token not in json.dumps(result, ensure_ascii=False)


def test_gold_timeout_keeps_independent_source_and_no_indicators(mock_http, monkeypatch):
    configure_gold(monkeypatch)
    def fail(request):
        raise httpx.ReadTimeout('fixture timeout', request=request)
    calls = mock_http(fail)
    result = market.evidence('XAU/USD', NOW)
    assert result['status'] == 'UNAVAILABLE' and result['reason'] == 'CANDLE_HTTP_UNAVAILABLE'
    assert result['candle_source'] == 'operator.example:spot:XAU/USD'
    assert result['price_indicators'] is None and len(calls) == 1


@pytest.mark.parametrize('mutation', ['warmup', 'stale'])
def test_gold_insufficient_or_stale_price_history_is_unavailable_for_indicators(mock_http, monkeypatch, mutation):
    configure_gold(monkeypatch)
    response = gold_payload()
    if mutation == 'warmup':
        response['bars'] = response['bars'][-99:]
    else:
        for bar in response['bars']:
            bar['open_time'] -= 120000
    mock_http(lambda _: httpx.Response(200, json=response))
    result = market.evidence('XAU/USD', NOW)
    assert result['status'] == ('INSUFFICIENT_DATA' if mutation == 'warmup' else 'STALE_DATA')
    assert result['price_indicators'] is None
