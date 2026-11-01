# Multi-product / multi-timeframe research

## Contracts and safety

`POST /api/v1/research/matrices` accepts an explicit UUID request_id, supported symbols,
timeframes, 1–8 versioned strategy variants, aware start_at/as_of and the existing cost config.
It creates the full Cartesian product, bounded at 20 tasks, 5000 bars per task and 20000
total task bars. Date boundaries must align to every selected timeframe (including zero
microseconds). UTC midnight dates in the UI satisfy every currently supported timeframe.
All products/strategies use the same calendar window, rather than the same number of bars.

The API reads all market windows under one PostgreSQL repeatable-read snapshot. Complete,
closed, matching, contiguous history and actual strategy warmup plus a next-open bar are
required for each group. Missing history rejects the entire request; it is not filled,
interpolated or silently shortened. Each group freezes its own data/rule snapshot. Different
strategies within a product/timeframe use exactly that same dataset. Each task has independent
cash, inventory and indicator state; returns must not be summed as a portfolio return.

All inputs are validated before a shared admission lock/transaction inserts the matrix.
The existing 20-active-task queue limit remains. The request UUID plus canonical request
digest is checked under the same lock: identical retries return the accepted matrix,
including after completion; changing inputs under an accepted UUID is rejected. An initial
rejected request creates no tasks. Browser storage keeps the UUID and submitted request,
allowing refresh recovery and explicit safe retry after a network failure.

GET returns only projected JSON metrics/request fields, not every frozen dataset or full
report. Each successful member can download its original result and request bounded replay
through the existing verification endpoint. Sorting supports market, return and drawdown;
CSV exports ratios as fractions, with a header, fixed window, bar counts, dataset digests
and job IDs. Cancellation retains already-completed results and flags active tasks through
the existing fenced worker checkpoint mechanism.

The existing mathematical engine remains spot-next-open-v3. Expanding its admission bound
from 1000 to 5000 bars does not change its old outputs, execution model or manifest fields.
Holdout/walk-forward and result-verification bounds are aligned. Offline exports now allow
32 MiB, matching the retained report verifier; input snapshots remain bounded at 4 MiB.
Legacy v1/v2 runners retain their own original bounds and formulas.

## New Stochastic rule

Reviewed [ta momentum.py](https://github.com/bukosabino/ta/blob/a890410710a6e483c9ba08da7f3dd5089e4b9dff/ta/momentum.py)
at the pinned commit a890410710a6e483c9ba08da7f3dd5089e4b9dff, specifically
StochasticOscillator._run and stoch_signal. Its [MIT license](https://github.com/bukosabino/ta/blob/a890410710a6e483c9ba08da7f3dd5089e4b9dff/LICENSE)
was also read. This is an indicator reference, not a copied complete strategy or a claim
of matching upstream execution/performance. No upstream code or dependency is embedded.

Local stochastic_long_flat_v1 uses K=100*(close-lowest low)/(highest high-lowest low),
including the current finalized bar, and a simple mean of the most recent smooth valid
K values. Default period=14, smooth=3, lower=20, upper=80. At/below lower targets LONG;
at/above upper targets FLAT; between thresholds retains target. All parameters are strict
bounded integers with lower < upper. Warmup needs period+smooth-1 closes, plus a further
bar to execute. A zero high/low range returns no signal and clears smoothing, rather than
inventing an oversold reading; existing inventory is not force-liquidated. Decimal precision
is 60. Non-finite/mismatched OHLC or non-increasing/naive times are rejected.

The strategy is historical research only; Paper/replay remain EMA/SMA. Private exchange
permissions, live execution and AI configuration are unchanged. LIVE_TRADING remains false.

## Scoped real-history extension

The new importer reads only chosen products, timeframes and exact date windows. It first
checks source gaps and compares every overlap to canonical stored candles. Conflicts fail
without revision. Only missing rows are inserted, using the existing transactional storage
guard; no existing candle, accepted Paper ledger or saved report is rewritten. Dry-run is
the default. Example in the configured backend environment:

```sh
python -m scripts.import_research_history --symbols BTCUSDT ETHUSDT --timeframes 4h 1d \
  --start 2025-10-15T00:00:00+00:00 --end 2026-10-15T00:00:00+00:00
# Add --write only to insert the reported missing candles.
```

Source API history is not proof of authenticated provenance. Current instrument rules are
snapshotted; historical rule changes are not reconstructed. Descriptive in-sample comparisons
and longer history alone do not establish profitability or out-of-sample robustness.
