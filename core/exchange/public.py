"""Select a public-only market feed; never changes private-account authorization."""
import os
from core.exchange.binance import INTERVALS, MarketDataError, RateLimited, utc_ms
from core.exchange.catalog import BITGET_SYMBOLS

PROVIDER = os.environ.get('MARKET_PROVIDER', 'binance')
if PROVIDER not in {'binance', 'bitget'}:
    raise ValueError('Unsupported MARKET_PROVIDER')
SOURCE = PROVIDER + '.public'
BOOK_DEPTH = 150 if PROVIDER == 'bitget' else 1000
SYMBOLS = BITGET_SYMBOLS if PROVIDER == 'bitget' else {symbol: f'{PROVIDER}:spot:{base}-USDT' for symbol, base in [('BTCUSDT', 'BTC'), ('ETHUSDT', 'ETH')]}

def public_adapter():
    if PROVIDER == 'bitget':
        from core.exchange.bitget import BitgetPublic
        return BitgetPublic()
    from core.exchange.binance import BinancePublic
    return BinancePublic()
