# Public products and research rules

## Scope

Bitget public Spot research supports BTC, ETH, SOL, XRP, BNB, DOGE, ADA and AVAX against USDT.
All eight were checked against `/api/v2/spot/public/symbols` on 2026-10-15: status `online`.
This is a bounded supported universe, not all Bitget products. Instruments are still
validated against live exchange precision, minimum quantity and notional rules at startup.
Binance's existing two-product universe and private execution permissions are unchanged.
Watchlists are browser-local; at least one product remains selected. Removing the active
product selects the first remaining watchlist item. Search does not invent unsupported markets.

The adapter budgets ten public requests/second and two historical requests/second per
process. Newly closed database bars come from the historical endpoint after a three-second
publication grace, once per interval boundary; live intrabar snapshots are not persisted.
Startup backfill excludes buckets closed less than 60 seconds ago. At runtime a closed
historical bar must also match the current-candle endpoint before it is stored. A live
2026-10-15 SOL sample disagreed at 17–22 seconds after close and agreed from about 25 seconds.
New Bitget realtime Paper accounts therefore default to the already-supported 60-second
lag budget (Binance remains 15); their frozen request records the chosen limit. Late bars
outside that limit still cannot generate simulated fills. Existing account limits are unchanged.
Conflicting closed bars keep the existing two-observation, at-least-60-second correction
gate and audit trail. A pending correction withholds that candle channel while independent
validated ticker/book snapshots continue. It does not authorize Paper or live execution.

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

## Additional GitHub research: drawdown diagnostics

Reviewed and pinned on 2026-10-15:

- [QuantStats stats.py](https://github.com/ranaroussi/quantstats/blob/9ef4c6d0a4ffe7831f6ebba6a5824fd33a90c05a/quantstats/stats.py),
  `ulcer_index` and `recovery_factor`; [Apache-2.0 license](https://github.com/ranaroussi/quantstats/blob/9ef4c6d0a4ffe7831f6ebba6a5824fd33a90c05a/LICENSE.txt).
- [ffn core.py](https://github.com/pmorissette/ffn/blob/383fc82319ee8d1a58300457dcf01ded2fd2049c/ffn/core.py),
  `to_drawdown_series` and `drawdown_details`; [MIT license](https://github.com/pmorissette/ffn/blob/383fc82319ee8d1a58300457dcf01ded2fd2049c/LICENSE).

Independent TypeScript implementation, no copied upstream source or installed dependency.
`drawdown-report-v1` reads the original frozen result's positive drawdown fractions and
compounded total return. It never rewrites the stored engine result, run_id or verification hash.
The separate JSON export includes its version, source run_id, source links and conventions.

Explicit differences from upstream (results are not claimed to match those libraries):

- Ulcer is population RMS `sqrt(sum(dd²)/N)`, including all observed closes. QuantStats' pinned
  implementation divides by N-1 instead.
- Return/drawdown is signed compounded total return / maximum drawdown; no drawdown yields
  null. The pinned QuantStats recovery factor uses absolute summed returns, which would
  hide a loss sign, so this platform deliberately uses a different label and formula.
- An episode starts at the first underwater observation, ends at the first observed recovery
  to the high-water mark, or the final observed close. A trailing episode remains explicitly
  unrecovered. Duration is actual elapsed seconds (not rounded calendar days); a single
  underwater final sample has zero observed duration, not a predicted recovery time.
- Underwater fraction counts samples, not wall-clock time. Empty/short, non-finite, blank,
  timezone-free, duplicate/out-of-order or out-of-range observations make the report unavailable.

Existing five strategies, Paper limits, frozen ledgers, backend and LIVE_TRADING=false are unchanged.
