# Portfolio observation comparability v1

Scope: read-only analysis of up to eight recent persisted snapshots belonging to one immutable independent Paper scenario. This is observation analysis, not continuous portfolio performance or an execution risk gate. GET `/api/v1/portfolio/scenarios/{id}/analysis?limit=8` (1..8) reads the frozen definition and recent records in one PostgreSQL REPEATABLE READ transaction. Storage time and UUID determine deterministic order. The response states if earlier records fall outside the window. No status filter silently removes bad observations.

Each envelope is validated and its original report fully reproduced before analysis. Cross-scenario mixtures, duplicate IDs, unordered storage times, broken hashes and unsupported formulas fail the entire analysis. Original snapshots and original valuation formula version remain unchanged. Exported input and the complete compact output are capped at 32 MiB; this is a resource limit rather than truncation of observations.

Each adjacent link is comparable only if both reports are COMPLETE, account read time advances, common price clocks advance by exactly 60 seconds, there is no worker-health warning, frozen account definitions and instrument rules match, revision does not regress, and old observations, accepted market data, orders, fills, ledger equity and signals are an exact prefix of the newer lists. All failed checks are retained as sorted reason codes. The observations and accepted-data prefix checks permit legitimate new trading observations and control revisions; they reject retroactive rewrites, including a retrospectively earlier manual halt that removes an already-recorded fill even if each individual report can reproduce. Account balances and initial capital are already reproduced from the frozen definition. Control status can legitimately change without changing cash provenance.

An UNAVAILABLE observation or failed comparison ends a segment. A later complete observation begins a new segment. Same-minute captures remain visible and split segments; they are never quietly deduplicated. The interval records seconds and unobserved intervening minutes. A missing saved observation is not a claim of lost market data or missing trades. No interpolation fills it. Single-point complete segments have zero observed change, not a zero strategy return. Window boundaries can cut a longer segment; no inference about the preceding history is made.

Decimal calculations use precision 120. Comparable links and segments report absolute equity changes in USDT. No percent return, annualization, Sharpe, cumulative drawdown or continuous equity curve is computed. Exact strings are used in the UI and exports. No aggregate delta crosses a gap, unavailable point or definition/source change. No current account or live market data is read during saved-history analysis; worker health is the original saved warning.

The page explicitly requests an analysis and labels its bounded window. A new request clears the old analysis before fetching; scene changes remount and abort the old request. Raw export preserves all inputs and the SHA-256 input digest. Offline verification:

```sh
uv run --frozen python -m scripts.verify_portfolio_continuity export.json
```

The verifier recomputes every envelope, link, segment and summary. It proves internal consistency, not signatures, user identity, storage authenticity, presence of all historical records, or current account health. Database administrators can defeat append-only triggers. Full multiuser auth, shared capital, OMS/Risk and Live remain incomplete.
