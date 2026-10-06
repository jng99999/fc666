# Segment-scoped sampled equity v1

GET `/api/v1/portfolio/scenarios/{id}/sampled?limit=8` adds an independent `paper-portfolio-sampled-v1` analysis. Inputs remain the original continuity-v1 frozen inputs: latest 1..8 saved snapshots, explicit earlier-window flag, storage-order chronology, full scenario and complete reports. Original valuation/history/continuity versions and exports remain compatible. No schema, dependency, service, credential or host changes.

Continuity-v1 reconstructs each envelope and sets all segment boundaries first. Within each COMPLETE segment, the peak at observation i is the maximum equity among that segment's observations up to i. Observed-point drawdown amount = peak − equity. Ratio = amount / peak only where peak > 0. All calculations use Decimal precision 120. A segment with fewer than two points returns null drawdown metrics and INSUFFICIENT_OBSERVATIONS; a multi-point segment with no positive peak returns null ratio with NO_POSITIVE_PEAK. Zero equity after a positive peak produces ratio 1. Equal peaks retain the first matching peak ID. Amount and ratio maxima retain separate peak/trough IDs because their maximizing intervals can differ. Flat measured segments report zero and no nonzero drawdown interval.

Every segment starts afresh, including at a bounded-window cutoff. No global drawdown combines segments. Unavailable points remain null and separate all adjacent comparisons. Missing observations, duplicate-minute records, worker-health warnings, source/ledger changes and clock regression retain all continuity-v1 breaks. Observe that sampled metrics omit unknown intraminute movements and earlier-window peaks; they do not establish actual continuous drawdown, risk acceptance, strategy returns or annualized results.

The UI shows separate connecting paths per comparable segment. Its x-axis is storage order, not a continuous time axis; straight connections are visual guides between observations, not inferred intermediate balances. Missing points are crosses in a separate band, not zero-equity points. Normalized plot heights are computed with Decimal from the selected window and converted only to bounded display coordinates. Flat domains use midpoint 0.5. Exact domain values, point equity and metrics remain strings in the table/export. Focus/hover exposes the precise point. Failed reload clears prior plot, metrics and export; scene changes abort and remount analysis state.

The complete compact JSON export is capped at 32 MiB and includes all source envelopes. Verify without database or current market access:

```sh
uv run --frozen python -m scripts.verify_portfolio_sampled export.json
```

The verifier reconstructs original reports, segment boundaries, all sampled metrics, interval identities and normalized heights. Internal consistency does not prove provenance, identity or absence of omitted historical records. This read-only feature does not modify balances, controls, rules, orders or stored snapshots. Live and complete portfolio execution risk remain unimplemented.
