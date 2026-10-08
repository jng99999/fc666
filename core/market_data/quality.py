"""Bounded public-data quality evidence, not execution authorization."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from core.exchange.binance import SYMBOLS
from core.models import Candle, Instrument
from core.storage.models import CandleRecord, InstrumentRecord
from core.paper.fault_adapter import encoded, sha

VERSION = 'public-spot-data-quality-v1'
EVIDENCE_VERSION = 'public-spot-data-quality-evidence-v1'
MAX_BYTES = 256 * 1024
MAX_SNAPSHOT_BYTES = 32 * 1024
LIVE_MAX_AGE = timedelta(seconds=15)
FUTURE_SKEW = timedelta(seconds=2)
BAR_MAX_AGE = timedelta(seconds=75)
ENVELOPE_KEYS = {'schema_version', 'type', 'instrument_id', 'symbol', 'channel',
                 'generation', 'sequence', 'event_time', 'received_at', 'quality', 'payload'}


def clock(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Bounded aware timestamp required')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Aware timestamp required')
    return result.astimezone(timezone.utc)


def number(value, *, positive=True):
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError('Bounded decimal string required')
    result = Decimal(value)
    if (not result.is_finite() or abs(result) > Decimal('1e40')
            or abs(result.as_tuple().exponent) > 80 or len(result.as_tuple().digits) > 80
            or (positive and result <= 0)):
        raise ValueError('Invalid market decimal')
    return result


def snapshot(value, symbol, channel, now):
    if (not isinstance(value, dict) or set(value) != ENVELOPE_KEYS
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['type'] != 'snapshot' or value['instrument_id'] != SYMBOLS[symbol]
            or value['symbol'] != symbol or value['channel'] != channel
            or value['quality'] != 'healthy'
            or not isinstance(value['generation'], str) or not 1 <= len(value['generation']) <= 128
            or type(value['sequence']) is not int or value['sequence'] < 1):
        raise ValueError('Invalid snapshot envelope')
    received, event = clock(value['received_at']), clock(value['event_time'])
    reasons = []
    if received - now > FUTURE_SKEW or event - now > FUTURE_SKEW or event - received > FUTURE_SKEW:
        reasons.append(channel.upper() + '_FUTURE_CLOCK')
    if now - received > LIVE_MAX_AGE or now - event > LIVE_MAX_AGE:
        reasons.append(channel.upper() + '_STALE')
    payload = value['payload']
    if channel == 'ticker':
        if not isinstance(payload, dict) or set(payload) != {'price', 'change_percent', 'volume', 'event_time'}:
            raise ValueError('Invalid ticker payload')
        number(payload['price']); number(payload['change_percent'], positive=False)
        if number(payload['volume'], positive=False) < 0 or clock(payload['event_time']) != event:
            raise ValueError('Invalid ticker metadata')
    else:
        if (not isinstance(payload, dict) or set(payload) != {'valid', 'exchange_sequence', 'snapshot_depth_limit', 'bids', 'asks'}
                or payload['valid'] is not True or type(payload['exchange_sequence']) is not int
                or payload['exchange_sequence'] < 0 or type(payload['snapshot_depth_limit']) is not int
                or payload['snapshot_depth_limit'] != 1000):
            raise ValueError('Invalid book metadata')
        prices = {}
        for side in ('bids', 'asks'):
            rows = payload[side]
            if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
                raise ValueError('Bounded complete book sides required')
            levels = []
            for row in rows:
                if not isinstance(row, list) or len(row) != 2:
                    raise ValueError('Book level pair required')
                levels.append(number(row[0])); number(row[1])
            if levels != sorted(set(levels), reverse=side == 'bids'):
                raise ValueError('Ordered unique book levels required')
            prices[side] = levels
        if prices['bids'][0] >= prices['asks'][0]:
            raise ValueError('Crossed book')
    return reasons


def live_reasons(symbol, ticker, book, observed_at):
    if symbol not in SYMBOLS:
        raise ValueError('Supported public symbol required')
    now = clock(observed_at); reasons = []
    for channel, value in [('ticker', ticker), ('book', book)]:
        if value is None:
            reasons.append(channel.upper() + '_MISSING')
        else:
            try:
                reasons.extend(snapshot(value, symbol, channel, now))
            except (ValueError, TypeError, KeyError, ArithmeticError):
                reasons.append(channel.upper() + '_INVALID')
    if not any(reason.endswith(('_INVALID', '_MISSING')) for reason in reasons):
        if ticker['generation'] != book['generation']:
            reasons.append('GENERATION_MISMATCH')
    return sorted(set(reasons))


def evaluate(evidence):
    keys = {'version', 'symbol', 'observed_at', 'cache_available', 'storage_available',
            'ticker', 'book', 'instrument', 'candles'}
    if (not isinstance(evidence, dict) or set(evidence) != keys
            or evidence['version'] != EVIDENCE_VERSION or evidence['symbol'] not in SYMBOLS
            or type(evidence['cache_available']) is not bool or type(evidence['storage_available']) is not bool
            or not isinstance(evidence['candles'], list) or len(evidence['candles']) > 3
            or len(encoded(evidence).encode()) > MAX_BYTES):
        raise ValueError('Invalid bounded quality evidence')
    symbol = evidence['symbol']; now = clock(evidence['observed_at'])
    reasons = live_reasons(symbol, evidence['ticker'], evidence['book'], evidence['observed_at'])
    if not evidence['cache_available']:
        reasons.append('CACHE_UNAVAILABLE')
    if not evidence['storage_available']:
        reasons.append('STORAGE_UNAVAILABLE')
    else:
        try:
            rules = Instrument.model_validate(evidence['instrument'])
            if (rules.instrument_id != SYMBOLS[symbol] or rules.exchange != 'binance'
                    or rules.native_symbol != symbol or rules.market_type != 'SPOT'
                    or rules.base != symbol[:-4] or rules.quote != 'USDT'):
                raise ValueError('Instrument identity mismatch')
            for channel, snap in [('ticker', evidence['ticker']), ('book', evidence['book'])]:
                if snap is not None and channel.upper() + '_INVALID' not in reasons:
                    payload = snap['payload']
                    prices = ([payload['price']] if channel == 'ticker' else
                              [row[0] for row in payload['bids'] + payload['asks']])
                    # The stored rules are observational, not venue-fresh proof.
                    from decimal import localcontext
                    with localcontext() as context:
                        context.prec = 160
                        if any(number(price) % rules.tick_size for price in prices):
                            reasons.append('PRICE_RULE_MISMATCH')
        except (ValueError, TypeError, KeyError, ArithmeticError):
            reasons.append('INSTRUMENT_OR_PRICE_INVALID')
        try:
            if any(not isinstance(value,dict) or value.get('is_closed') is not True
                   or not isinstance(value.get('open_time'),str) or not isinstance(value.get('close_time'),str)
                   for value in evidence['candles']):
                raise ValueError('Explicit finalized candle evidence required')
            bars = [Candle.model_validate(value) for value in evidence['candles']]
            if len(bars) != 3:
                reasons.append('INSUFFICIENT_CLOSED_BARS')
            if any(bar.instrument_id != SYMBOLS[symbol] or bar.timeframe != '1m'
                   or not bar.is_closed or bar.close_time > now or bar.source != 'binance.public' for bar in bars):
                reasons.append('CLOSED_BAR_IDENTITY_OR_FINALITY_INVALID')
            if any(right.open_time != left.close_time for left, right in zip(bars, bars[1:])):
                reasons.append('CLOSED_BAR_GAP')
            if bars and now - bars[-1].close_time > BAR_MAX_AGE:
                reasons.append('CLOSED_BARS_STALE')
        except (ValueError, TypeError, KeyError, ArithmeticError):
            reasons.append('CLOSED_BARS_INVALID')
    reasons = sorted(set(reasons))
    body = {'version': VERSION, 'evidence': deepcopy(evidence), 'instrument_id': SYMBOLS[symbol],
            'status': 'healthy' if not reasons else 'unavailable', 'reasons': reasons,
            'data_quality_checks_passed': not reasons,
            'limits': {'live_age_seconds': 15, 'future_skew_seconds': 2,
                       'closed_bar_age_seconds': 75, 'closed_bar_count': 3, 'timeframe': '1m'},
            'cross_store_atomic_capture': False, 'venue_origin_authenticated': False,
            'instrument_rules_freshness_verified': False, 'read_only': True, 'submission_allowed': False}
    report = {**body, 'sha256': sha(body)}
    if len(encoded(report).encode()) > MAX_BYTES:
        raise ValueError('Quality report exceeds 256 KiB')
    return report


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def decode(raw):
    if raw is None:
        return None
    if not isinstance(raw, (str, bytes)) or len(raw.encode() if isinstance(raw,str) else raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError('Bounded raw snapshot required')
    def reject_constant(_):
        raise ValueError('Nonfinite JSON number')
    return json.loads(raw, object_pairs_hook=unique_object,parse_constant=reject_constant)


def capture(engine, cache, symbol):
    if symbol not in SYMBOLS or engine.dialect.name != 'postgresql':
        raise ValueError('PostgreSQL and supported symbol required')
    evidence = {'version': EVIDENCE_VERSION, 'symbol': symbol, 'observed_at': None,
                'cache_available': True, 'storage_available': True,
                'ticker': None, 'book': None, 'instrument': None, 'candles': []}
    try:
        # Rules and bars share a read-only repeatable PostgreSQL snapshot.
        with Session(engine) as db, db.begin():
            if db.connection().connection.driver_connection.autocommit:
                raise ValueError('Autocommit cannot provide a coherent storage snapshot')
            db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            db.execute(text("SET LOCAL statement_timeout = '2000ms'"))
            rules = db.get(InstrumentRecord, SYMBOLS[symbol])
            if rules is not None:
                evidence['instrument'] = Instrument.model_validate(rules, from_attributes=True).model_dump(mode='json')
            cutoff = db.scalar(text('SELECT clock_timestamp()'))
            query = select(CandleRecord).where(CandleRecord.instrument_id == SYMBOLS[symbol],
                    CandleRecord.timeframe == '1m', CandleRecord.is_closed.is_(True),
                    CandleRecord.close_time <= cutoff).order_by(CandleRecord.open_time.desc()).limit(3)
            evidence['candles'] = [Candle.model_validate(bar, from_attributes=True).model_dump(mode='json')
                                  for bar in reversed(list(db.scalars(query)))]
    except Exception:
        evidence.update(storage_available=False, instrument=None, candles=[])
    try:
        raw = cache.mget([f'market:{symbol}:ticker', f'market:{symbol}:book'])
        evidence['ticker'], evidence['book'] = [decode(value) for value in raw]
    except Exception:
        evidence.update(cache_available=False, ticker=None, book=None)
    evidence['observed_at'] = datetime.now(timezone.utc).isoformat()
    return evaluate(evidence)


def verify(report):
    if (not isinstance(report, dict) or report.get('version') != VERSION
            or len(encoded(report).encode()) > MAX_BYTES):
        raise ValueError('Invalid quality report envelope/version/bounds')
    expected = evaluate(report.get('evidence'))
    if encoded(report) != encoded(expected):
        raise ValueError('Quality report differs from complete replay')
    return deepcopy(expected)
