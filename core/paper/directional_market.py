"""Public quote inputs for synthetic paper accounts; never a broker/order client."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import time

import httpx

from core.exchange.catalog import BITGET_SYMBOLS
from core.gold.market import SOURCE as GOLD_SOURCE, fetch_quote as fetch_gold
from core.paper.directional import Quote

BITGET_SOURCE = 'bitget.public:spot:orderbook:step0'
INSTRUMENTS = tuple(BITGET_SYMBOLS) + ('XAU/USD',)


def source_for(instrument):
    if instrument == 'XAU/USD':
        return GOLD_SOURCE
    if instrument in BITGET_SYMBOLS:
        return BITGET_SOURCE
    raise ValueError('Unsupported directional paper instrument')


def parse_book(instrument, payload, now):
    """Use the order-book timestamp and executable top levels, not last trade."""
    if instrument not in BITGET_SYMBOLS or not isinstance(payload, dict) or payload.get('code') != '00000':
        raise ValueError('Invalid Bitget book envelope')
    try:
        row = payload['data']
        if (not isinstance(row, dict) or not isinstance(row.get('ts'), str) or not row['ts'].isdigit()
                or not isinstance(row.get('bids'), list) or not isinstance(row.get('asks'), list)
                or len(row['bids']) != 1 or len(row['asks']) != 1):
            raise ValueError('One timestamped bid/ask level required')
        for levels in (row['bids'], row['asks']):
            if (not isinstance(levels[0], list) or len(levels[0]) != 2
                    or any(not isinstance(value, str) for value in levels[0])):
                raise ValueError('Exact string quote and size required')
            size = Decimal(levels[0][1])
            if not size.is_finite() or size <= 0:
                raise ValueError('Positive executable top-level size required')
        stamp = int(row['ts'])
        if not 0 <= now - stamp <= 60000:
            raise ValueError('Fresh nonfuture Bitget quote required')
        return Quote(instrument=instrument, source=BITGET_SOURCE,
                     at=datetime.fromtimestamp(stamp // 1000, timezone.utc) + timedelta(milliseconds=stamp % 1000),
                     bid=row['bids'][0][0], ask=row['asks'][0][0])
    except (KeyError, TypeError, IndexError, InvalidOperation, OverflowError) as error:
        raise ValueError('Invalid public Bitget order book') from error


def fetch_quote(instrument):
    source_for(instrument)
    if instrument == 'XAU/USD':
        value = fetch_gold()
        return Quote(instrument=instrument, source=GOLD_SOURCE,
                     at=datetime.fromtimestamp(value.ts // 1000, timezone.utc) + timedelta(milliseconds=value.ts % 1000),
                     bid=value.bid, ask=value.ask)
    with httpx.Client(timeout=6, follow_redirects=False) as client:
        response = client.get('https://api.bitget.com/api/v2/spot/market/orderbook',
                              params={'symbol': instrument, 'type': 'step0', 'limit': '1'})
        response.raise_for_status()
        if len(response.content) > 128000:
            raise ValueError('Oversized Bitget book response')
        return parse_book(instrument, response.json(), int(time.time() * 1000))


def instruments():
    return [{'instrument': value, 'quote_source': source_for(value),
             'currency': 'USD' if value == 'XAU/USD' else 'USDT',
             'unit': 'troy_ounce' if value == 'XAU/USD' else value[:-4],
             'contract': 'SYNTHETIC_FULLY_COLLATERALIZED_1X',
             'broker_execution': False} for value in INSTRUMENTS]
