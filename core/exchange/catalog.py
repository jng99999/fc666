"""Bounded public research universe, independent of private trading permissions."""
BITGET_SYMBOLS = {base + 'USDT': f'bitget:spot:{base}-USDT'
                  for base in ('BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE', 'ADA', 'AVAX')}
