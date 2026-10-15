"""Bitget v2 public Spot REST snapshots. No keys, signing, or order methods."""
from decimal import Decimal
import ssl
import threading
import time
import httpx
from core.exchange.binance import INTERVALS, MarketDataError, RateLimited, utc_ms
from core.models import Candle, Instrument

SYMBOLS = {'BTCUSDT': 'bitget:spot:BTC-USDT', 'ETHUSDT': 'bitget:spot:ETH-USDT'}
GRANULARITY = {'1m': '1min', '5m': '5min', '15m': '15min', '1h': '1h', '4h': '4h', '1d': '1Dutc'}

def normalize_candle(symbol, interval, row, server_time):
    if symbol not in SYMBOLS or interval not in INTERVALS or not isinstance(row, list) or len(row) < 6:
        raise MarketDataError('Invalid Bitget candle identity or row')
    opened = int(row[0]); closed = opened + INTERVALS[interval]
    return Candle(instrument_id=SYMBOLS[symbol], timeframe=interval,
        open_time=utc_ms(opened), close_time=utc_ms(closed), open=row[1], high=row[2],
        low=row[3], close=row[4], volume=row[5], is_closed=closed <= server_time, source='bitget.public')

class BitgetPublic:
    def __init__(self, client=None):
        self.owned = client is None
        self.client = client or httpx.Client(base_url='https://api.bitget.com', timeout=15,
            verify=ssl.create_default_context(), follow_redirects=False)
        self.lock = threading.Lock()
        self.next_request = 0.0

    def close(self):
        if self.owned:
            self.client.close()

    def get(self, path, params=None):
        # Shared across both symbol threads: at most five public requests/second.
        with self.lock:
            delay = self.next_request - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_request = time.monotonic() + .2
        try:
            response = self.client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise MarketDataError('Bitget public REST unavailable') from exc
        if response.status_code == 429:
            try: delay = max(1, float(response.headers.get('Retry-After', '60')))
            except ValueError: delay = 60
            with self.lock:
                self.next_request = max(self.next_request, time.monotonic() + delay)
            raise RateLimited(delay)
        if response.status_code != 200:
            raise MarketDataError(f'Bitget public REST HTTP {response.status_code}')
        try:
            envelope = response.json()
            if envelope['code'] != '00000' or envelope['data'] is None:
                raise ValueError('Rejected response')
            return envelope['data']
        except (ValueError, KeyError, TypeError) as exc:
            raise MarketDataError('Invalid Bitget public response') from exc

    def server_time(self):
        value = int(self.get('/api/v2/public/time')['serverTime'])
        utc_ms(value)
        return value

    def instruments(self):
        result = []
        for symbol, identity in SYMBOLS.items():
            rows = self.get('/api/v2/spot/public/symbols', {'symbol': symbol})
            if len(rows) != 1 or rows[0]['symbol'] != symbol or rows[0]['status'] != 'online':
                raise MarketDataError('Bitget spot instrument unavailable')
            row = rows[0]
            if row['baseCoin'] + row['quoteCoin'] != symbol or row['quoteCoin'] != 'USDT':
                raise MarketDataError('Unexpected Bitget instrument')
            result.append(Instrument(instrument_id=identity, exchange='bitget', native_symbol=symbol,
                base=row['baseCoin'], quote=row['quoteCoin'], market_type='SPOT',
                tick_size=Decimal(10) ** -int(row['pricePrecision']),
                quantity_step=Decimal(10) ** -int(row['quantityPrecision']),
                min_quantity=row['minTradeAmount'], min_notional=row['minTradeUSDT']))
        return result

    def candles(self, symbol, interval, start, end, server_time):
        if symbol not in SYMBOLS or interval not in GRANULARITY or start >= end:
            raise ValueError('Invalid Bitget historical range')
        cursor = end; seen = {}
        # History endpoint requires endTime and pages backwards, up to 200 rows.
        while cursor > start:
            rows = self.get('/api/v2/spot/market/history-candles', {'symbol': symbol,
                'granularity': GRANULARITY[interval], 'endTime': str(cursor), 'limit': '200'})
            if not rows:
                break
            earliest = min(int(row[0]) for row in rows)
            if earliest >= cursor:
                raise MarketDataError('Bitget candle pagination did not progress')
            for row in rows:
                candle = normalize_candle(symbol, interval, row, server_time)
                opened = int(row[0])
                if start <= opened < end and candle.is_closed:
                    if opened in seen and seen[opened] != candle:
                        raise MarketDataError('Conflicting Bitget historical candle')
                    seen[opened] = candle
            # Bitget rounds endTime to the candle boundary; subtracting 1ms skips a bar.
            cursor = earliest
        return [seen[key] for key in sorted(seen)]

    def recent_candles(self, symbol, interval, now):
        rows = self.get('/api/v2/spot/market/candles', {'symbol': symbol,
            'granularity': GRANULARITY[interval], 'limit': '3'})
        return sorted([normalize_candle(symbol, interval, row, now) for row in rows], key=lambda bar: bar.open_time)

    def snapshots(self, symbol):
        ticker_rows = self.get('/api/v2/spot/market/tickers', {'symbol': symbol})
        if len(ticker_rows) != 1 or ticker_rows[0]['symbol'] != symbol:
            raise MarketDataError('Unexpected Bitget ticker')
        row = ticker_rows[0]; event = utc_ms(int(row['ts'])).isoformat()
        ticker = {'price': row['lastPr'], 'change_percent': str(Decimal(row['change24h']) * 100),
            'volume': row['baseVolume'], 'event_time': event}
        depth = self.get('/api/v2/spot/market/orderbook', {'symbol': symbol, 'type': 'step0', 'limit': '150'})
        # Every REST response is a complete independent snapshot, not a reconstructed delta book.
        from core.market_data.orderbook import OrderBook
        book = OrderBook()
        book.snapshot({'lastUpdateId': int(depth['ts']), 'bids': depth['bids'], 'asks': depth['asks']})
        book.valid = True
        view = book.view(); view['snapshot_depth_limit'] = 150
        rows = self.get('/api/v2/spot/market/fills', {'symbol': symbol, 'limit': '100'})
        trades = []
        for trade in reversed(rows):
            if trade['symbol'] != symbol or trade['side'] not in {'buy', 'sell'} or not trade['tradeId'].isdigit():
                raise MarketDataError('Unexpected Bitget trade')
            trades.append({'trade_id': trade['tradeId'], 'price': trade['price'], 'quantity': trade['size'],
                'aggressor_side': trade['side'].upper(), 'event_time': utc_ms(int(trade['ts'])).isoformat()})
        return [('ticker', ticker, event), ('book', view, utc_ms(int(depth['ts'])).isoformat()),
            ('trade', {'trades': trades}, trades[-1]['event_time'] if trades else event)]
