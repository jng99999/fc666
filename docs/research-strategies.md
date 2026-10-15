# Public products and research rules

## Scope

Bitget public Spot research supports BTC, ETH, SOL, XRP, BNB, DOGE, ADA and AVAX against USDT.
All eight were checked against `/api/v2/spot/public/symbols` on 2026-10-15: status `online`.
This is a bounded supported universe, not all Bitget products. Instruments are still
validated against live exchange precision, minimum quantity and notional rules at startup.
Binance's existing two-product universe and private execution permissions are unchanged.
Watchlists are browser-local; at least one product remains selected. Removing the active
product selects the first remaining watchlist item. Search does not invent unsupported markets.

## Source review

Reviewed QuantConnect/LEAN at commit `80e7843f645673bcbeaab963049f76f20f6785e1`, Apache-2.0:

- [RSI alpha model](https://github.com/QuantConnect/Lean/blob/80e7843f645673bcbeaab963049f76f20f6785e1/Algorithm.Framework/Alphas/RsiAlphaModel.py)
- [Bollinger indicator](https://github.com/QuantConnect/Lean/blob/80e7843f645673bcbeaab963049f76f20f6785e1/Indicators/BollingerBands.cs)
- [Donchian indicator](https://github.com/QuantConnect/Lean/blob/80e7843f645673bcbeaab963049f76f20f6785e1/Indicators/DonchianChannel.cs)
- [License](https://github.com/QuantConnect/Lean/blob/80e7843f645673bcbeaab963049f76f20f6785e1/LICENSE)

These are references for mathematical rules, not an imported LEAN runtime or a claim
that its alpha state machine and execution model have been reproduced. No upstream code
or new dependency was embedded. backtesting.py was also reviewed but not embedded (AGPL-3.0).

## Local versioned adaptations

All strategies target LONG or FLAT only, decide on closed bars, execute at the next bar
open under the existing fee/slippage/prior-volume/tick/minimum-notional model. No leverage,
short sales, private exchange access or live orders. Initial state is flat.

| Strategy ID | Parameters | Rule and warmup |
| --- | --- | --- |
| ema_long_flat_v1 | period=20 | Existing SMA-seeded EMA; close above EMA long, else flat; period closes |
| sma_long_flat_v1 | fast=10, slow=20 | Existing fast SMA above slow SMA long, else flat; slow closes |
| rsi_long_flat_v1 | period=14, lower=30, upper=70 | Wilder smoothed gains/losses, simple-average seed; RSI ≤ lower enters, ≥ upper exits, otherwise holds; period+1 closes; constant series RSI=50 |
| bollinger_long_flat_v1 | period=20, deviations=2 | Simple mean ± multiplier × population standard deviation; close below lower enters, ≥ mean exits, otherwise holds; period closes; zero variance does not enter |
| channel_long_flat_v1 | entry=20, exit=10 | Close above highest high of previous entry bars enters; below lowest low of previous exit bars exits; equality holds; current bar excluded; entry+1 closes |

RSI/Bollinger/channel are historical research only. Existing Paper/replay strategy
support remains EMA/SMA, explicitly validated by their request models. Additional products
are usable in these workflows. Holdout and walk-forward enforce the strategy's actual warmup
plus a next-open execution bar in every independent segment. No parameter uses future data.
All numbers are strict bounded integers, RSI lower < upper, SMA fast < slow, channel exit ≤ entry.

These are baselines, not profitable strategy recommendations. Mean reversion can accumulate
losses in trends; breakout systems can whipsaw. Compare costs, holdouts and turnover, not only
in-sample returns. AI is unconfigured and live trading remains disabled.
