# Mobile chart periods and workbench

The existing main checkout owns these changes. No execution interval, exchange order API, account ledger, news gate, server secret or deployment environment setting is changed.

## Behavior

- Quotes and depth belong to `/`; deterministic evidence and indicator configuration belong to `/analysis`. Market no longer duplicates the analysis cards and detailed reports.
- Both pages expose 1m, 2m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 1d and custom integer 1–60 minutes / 1–24 hours. Native intervals retain their existing API. Other intervals page backwards through the largest supported exact divisor, up to eight pages / 8,000 source bars.
- Custom periods use UTC-aligned OHLCV: first open, maximum high, minimum low, final close, summed volume. Only complete buckets enter indicators and structure analysis. Leading/trailing partial buckets are excluded, interior gaps are rejected and the latest completed result still passes the freshness guard. Delayed final source candles are never padded.
- Indicators and causal structure are recomputed after aggregation. Native-period indicators are not relabelled. This is a chart/research choice, not a new backtest or execution timeframe contract.
- The immersive chart retains period controls, indicators, volume and reset. It uses the actual viewport when the device rotates; drawing/layer controls are below the price pane. Only one workbench is mounted, so full-screen transitions cannot race two drawing writers. Drawings remain scoped to source, product and period.
- Labels share collision layout across automatic regions, hand drawings, profile references and account/plan references. Selected regions receive priority. Hidden crowded labels retain their actual geometry and region selector.
- Shared selection dialogs keep their list height, respond to visual-viewport changes and do not focus search on pointer open. Keyboard opening/search, empty results, cancellation and return focus remain available.
- Gold uses the same period controls and immersive chart, retaining Swissquote sampled-mid provenance and unavailable trade volume. Session gaps remain gaps; incomplete buckets are never filled. The current server returns only 120 one-minute samples, so long gold periods can truthfully report insufficient data. HistData backtest history is not mixed into this live chart.

## Validation

Executed frontend tests: `NEXT_PUBLIC_MARKET_PROVIDER=bitget node --experimental-strip-types --test tests/*.test.mjs` — 176 passed, zero failures. `npm run typecheck` and `NEXT_PUBLIC_MARKET_PROVIDER=bitget npm run build` also passed during implementation; the final release is checked again after integration.

Ego Lite on the real local frontend and public backend verified actual 1m/2m/3m/5m selection; custom 7m; 3h analysis; one rule card on analysis and none on market; full-screen landscape sizing; drawing-menu options and search; adding a manual price box, restoring it after leaving/reopening full screen, and isolating it from another period. Gold 3m produced 39 complete samples from 120 minute samples; gold 2h displayed insufficient history. Public gold market reported `trading_enabled=false`.

Viewport emulation is not a real iPhone Safari, physical keyboard or operating-system orientation-lock test. The implementation supports rotation and immersive layout, without claiming iOS can be forced to rotate by a website. No live or paper orders were sent as part of this chart acceptance.
