"""Closed, venue-specific research candles; no orders or substitute markets."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import json
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

from core.exchange.catalog import BITGET_SYMBOLS
from core.gold.market import SOURCE as GOLD_SOURCE
from core.paper.directional_market import BITGET_SOURCE

BITGET_URL = 'https://api.bitget.com/api/v2/spot/market/candles'
CANDLE_SOURCE = 'bitget.public:spot:candles:1min'
INTERVAL_MS = 60000
MIN_BARS = 100
MAX_BARS = 200
MAX_RESPONSE_BYTES = 160000
HTTP_TIMEOUT = 6.0
MAX_AGE_MS = 90000


def _milliseconds(now: datetime | int) -> int:
    if isinstance(now, datetime):
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('Timezone-aware evidence time required')
        return int(now.timestamp() * 1000)
    if isinstance(now, bool) or not isinstance(now, int) or now < 0:
        raise ValueError('Integer epoch milliseconds required')
    return now


def _number(value: Any) -> Decimal:
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        raise ValueError('Bounded decimal strings required')
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError('Invalid candle number') from error
    if not result.is_finite() or abs(result.adjusted()) > 30:
        raise ValueError('Finite bounded candle numbers required')
    return result


def parse_candles(payload: Any, now: datetime | int, *, allow_null_volume: bool = False) -> list[dict[str, Any]]:
    """Normalize only closed 1m Bitget spot bars; reject gaps/conflicting repeats."""
    checked_at = _milliseconds(now)
    if (not isinstance(payload, dict) or payload.get('code') != '00000'
            or not isinstance(payload.get('data'), list) or len(payload['data']) > MAX_BARS):
        raise ValueError('Invalid bounded Bitget candle envelope')
    unique: dict[int, dict[str, Any]] = {}
    for raw in payload['data']:
        if not isinstance(raw, list) or len(raw) not in (7, 8):
            raise ValueError('Invalid Bitget candle row')
        stamp = raw[0]
        if not isinstance(stamp, str) or not stamp.isascii() or not stamp.isdigit() or len(stamp) > 16:
            raise ValueError('Integer candle timestamp required')
        opened_at = int(stamp)
        if opened_at % INTERVAL_MS:
            raise ValueError('Candle timestamp must align to one minute')
        if opened_at + INTERVAL_MS > checked_at:
            continue
        opened, high, low, close = (_number(value) for value in raw[1:5])
        volume = None if allow_null_volume and raw[5] is None else _number(raw[5])
        # Unused quote-volume fields are still checked; their units are not inferred.
        remaining_volumes = [_number(value) for value in raw[6:]]
        if (not 0 < low <= min(opened, close) <= max(opened, close) <= high
                or (volume is not None and volume < 0) or any(value < 0 for value in remaining_volumes)):
            raise ValueError('Invalid candle OHLC or volume bounds')
        bar = {'open_time': opened_at, 'close_time': opened_at + INTERVAL_MS,
               'open': str(opened), 'high': str(high), 'low': str(low),
               'close': str(close), 'volume': None if volume is None else str(volume)}
        previous = unique.get(opened_at)
        if previous is not None:
            # Numeric equivalence permits harmless alternate decimal formatting.
            if any((previous[key] is None) != (bar[key] is None)
                   or (previous[key] is not None and Decimal(previous[key]) != Decimal(bar[key]))
                   for key in ('open', 'high', 'low', 'close', 'volume')):
                raise ValueError('Conflicting duplicate candle')
        else:
            unique[opened_at] = bar
    bars = [unique[stamp] for stamp in sorted(unique)]
    if any(right['open_time'] != left['close_time'] for left, right in zip(bars, bars[1:])):
        raise ValueError('Noncontinuous candle history')
    return bars


def calculate_indicators(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """SMA-seeded EMA20 and Wilder RSI14/ATR14 over the supplied closed sequence.

    Decimal strings preserve reproducibility. RSI of an entirely flat sequence is
    neutral (50); ATR starts with the first 14 true ranges using previous closes.
    """
    if len(bars) < MIN_BARS or len(bars) > MAX_BARS:
        raise ValueError('100..200 closed bars required for research indicators')
    checked = parse_candles({'code': '00000', 'data': [
        [str(bar['open_time']), bar['open'], bar['high'], bar['low'],
         bar['close'], bar['volume'], '0'] for bar in bars]}, bars[-1]['close_time'], allow_null_volume=True)
    if checked != bars:
        raise ValueError('Ordered unique normalized closed bars required')
    with localcontext() as precision:
        precision.prec = 50
        closes = [Decimal(bar['close']) for bar in bars]
        ema = sum(closes[:20], Decimal(0)) / 20
        alpha = Decimal(2) / 21
        for close in closes[20:]:
            ema += alpha * (close - ema)
        changes = [right - left for left, right in zip(closes, closes[1:])]
        gains = [max(change, Decimal(0)) for change in changes]
        losses = [max(-change, Decimal(0)) for change in changes]
        gain = sum(gains[:14], Decimal(0)) / 14
        loss = sum(losses[:14], Decimal(0)) / 14
        ranges = [max(Decimal(bar['high']) - Decimal(bar['low']),
                      abs(Decimal(bar['high']) - previous),
                      abs(Decimal(bar['low']) - previous))
                  for previous, bar in zip(closes, bars[1:])]
        atr = sum(ranges[:14], Decimal(0)) / 14
        for next_gain, next_loss, next_range in zip(gains[14:], losses[14:], ranges[14:]):
            gain = (gain * 13 + next_gain) / 14
            loss = (loss * 13 + next_loss) / 14
            atr = (atr * 13 + next_range) / 14
        rsi = (Decimal(50) if gain == loss == 0 else Decimal(100) if loss == 0
               else 100 - 100 / (1 + gain / loss))
        return {'ema20': str(ema), 'rsi14': str(rsi), 'atr14': str(atr),
                'as_of': bars[-1]['close_time'], 'input_bars': len(bars),
                'method': 'decimal50; EMA20=SMA20-seeded; RSI14/ATR14=Wilder-SMA14-seeded; flat-RSI=50'}


def _fetch_payload(instrument: str, now_ms: int) -> Any:
    return _get_json(BITGET_URL, {'symbol': instrument, 'granularity': '1min', 'limit': str(MAX_BARS),
                                'endTime': str(now_ms // INTERVAL_MS * INTERVAL_MS - 1)})


def _get_json(url: str, params: dict[str, str], token: str = '', *, public_only: bool = False) -> Any:
    # Only caller-selected server configuration; no URL or token from research input.
    options = {}
    if public_only:
        from core.security.public_http import PublicTransport
        options['transport'] = PublicTransport()
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False, trust_env=False, **options) as client:
        with client.stream('GET', url, params=params,
                           headers={'Authorization': 'Bearer ' + token} if token else {}) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=16384):
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ValueError('Oversized candle response')
            return json.loads(content)


def _gold_configuration(settings=None) -> tuple[str, str, str] | None:
    url = settings.get('url', '') if settings is not None else os.getenv('FC666_GOLD_HISTORY_URL', '')
    source = settings.get('source', '') if settings is not None else os.getenv('FC666_GOLD_HISTORY_SOURCE', '')
    token = settings.get('token', '') if settings is not None else os.getenv('FC666_GOLD_HISTORY_TOKEN', '')
    if not url and not source and not token:
        return None
    parts = urlsplit(url)
    if (len(url) > 2048 or url != url.strip() or any(char.isspace() for char in url)
            or parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.fragment or '#' in url
            or not 0 < len(source) <= 256 or source != source.strip()
            or any(ord(char) < 32 for char in source)
            or len(token) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in token)):
        raise ValueError('Invalid server gold-history configuration')
    # Accessing .port rejects malformed or out-of-range ports before HTTP.
    _ = parts.port
    try:
        httpx.URL(url)
    except httpx.InvalidURL as error:
        raise ValueError('Invalid server gold-history URL') from error
    return url, source, token


def parse_gold_candles(payload: Any, now: datetime | int, source: str) -> list[dict[str, Any]]:
    """Operator-adapted independent XAU/USD spot prices, never futures substitutes."""
    if (not isinstance(payload, dict) or payload.get('instrument') != 'XAU/USD'
            or payload.get('product_type') != 'SPOT' or payload.get('currency') != 'USD'
            or payload.get('candle_source') != source or not isinstance(payload.get('bars'), list)
            or len(payload['bars']) > MAX_BARS):
        raise ValueError('Gold history market or source identity mismatch')
    rows = []
    for bar in payload['bars']:
        if not isinstance(bar, dict):
            raise ValueError('Gold history bars must be objects')
        stamp = bar.get('open_time')
        if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0:
            raise ValueError('Original integer source open_time required')
        rows.append([str(stamp), bar['open'], bar['high'], bar['low'], bar['close'], bar['volume'], '0'])
    return parse_candles({'code': '00000', 'data': rows}, now, allow_null_volume=True)


def evidence(instrument: str, now: datetime | int, *, gold_settings=None) -> dict[str, Any]:
    """Return JSON-safe evidence or an explicit unavailable state, never fake bars."""
    now_ms = _milliseconds(now)
    supported = instrument in BITGET_SYMBOLS
    result: dict[str, Any] = {
        'status': 'UNAVAILABLE', 'instrument': instrument, 'checked_at': now_ms,
        'quote_source': GOLD_SOURCE if instrument == 'XAU/USD' else BITGET_SOURCE if supported else None,
        'candle_source': CANDLE_SOURCE if supported else None,
        'currency': 'USD' if instrument == 'XAU/USD' else 'USDT' if supported else None,
        'candle_type': 'EXCHANGE_SPOT_OHLCV' if supported else None,
        'interval': '1m', 'required_bars': MIN_BARS, 'bars': [], 'indicators': None,
        'price_indicators': None, 'indicator_scope': 'PRICE_ONLY', 'volume_available': False,
        'trading_enabled': False,
    }
    if not supported and instrument != 'XAU/USD':
        result['reason'] = 'UNSUPPORTED_INSTRUMENT'
        return result
    try:
        if instrument == 'XAU/USD':
            result.update(configuration_status='UNAVAILABLE', product_type='SPOT',
                          volume_scope='PROVIDER_SPECIFIC_NOT_GLOBAL',
                          methodology='Independent XAU/USD spot price history; current quote is Swissquote '
                                      'AT/AT standard. Cross-source basis is unverified; no same-source claim.',
                          cross_source_basis_verified=False)
            configuration = _gold_configuration(gold_settings)
            if configuration is None:
                result['reason'] = 'HISTORICAL_CANDLES_UNAVAILABLE'
                return result
            url, source, token = configuration
            result.update(configuration_status='READY', candle_source=source,
                          candle_type='INDEPENDENT_SPOT_PRICE_HISTORY')
            bars = parse_gold_candles(_get_json(url, {
                'instrument': 'XAU/USD', 'interval': '1m', 'limit': str(MAX_BARS),
                'end_time': str(now_ms)}, token, public_only=gold_settings is not None), now_ms, source)
        else:
            bars = parse_candles(_fetch_payload(instrument, now_ms), now_ms)
        result['bars'] = bars
        result['volume_available'] = bool(bars) and all(bar['volume'] is not None for bar in bars)
        if len(bars) < MIN_BARS:
            result.update(status='INSUFFICIENT_DATA', reason='CLOSED_CANDLE_WARMUP')
        elif now_ms - bars[-1]['close_time'] > MAX_AGE_MS:
            result.update(status='STALE_DATA', reason='CLOSED_CANDLE_HISTORY_STALE')
        else:
            indicators = calculate_indicators(bars)
            result.update(status='READY', reason='CLOSED_CANDLES_READY',
                          indicators=indicators, price_indicators=indicators)
    except httpx.HTTPError:
        result.update(status='UNAVAILABLE', reason='CANDLE_HTTP_UNAVAILABLE')
    except (ValueError, KeyError, TypeError, InvalidOperation, OverflowError, RecursionError):
        if instrument == 'XAU/USD' and result.get('configuration_status') == 'UNAVAILABLE':
            result.update(configuration_status='INVALID', reason='GOLD_HISTORY_CONFIG_INVALID')
        else:
            result['reason'] = 'CANDLE_DATA_INVALID'
        result.update(status='INVALID_DATA', bars=[], indicators=None,
                      price_indicators=None, volume_available=False)
    return result
