# Immutable Paper portfolio history v1

`/portfolio/history` freezes a named scenario: 1..8 explicitly selected realtime Paper account UUIDs, their order, and exact Decimal risk hint limits. Historical replay accounts and shared/live capital are excluded. Definitions cannot be edited; create another scenario for different membership or limits. This page uses the current valuation page's locally saved account selection, but independently entered thresholds.

Migration 0009 adds scenarios and snapshots without rewriting previous accounts. UPDATE/DELETE triggers reject changes to both tables. Database administrators can remove these protections; hashes are not signatures or authentication. This local research environment does not provide multiuser isolation.

POST `/api/v1/portfolio/scenarios` accepts only `request_id` UUID and `definition` (name, session_ids, limits). Creation uses a global idempotency key: replay returns the original scenario; a different definition under the same key returns 409. Capacity is 100 scenarios. POST `/{scenario_id}/snapshots` accepts only a UUID request_id, scoped to that scenario. Replays return the original complete envelope, without recapturing newer data. Capacity is 200 snapshots globally; both bounds return 429. Transaction advisory locks serialize each write class and enforce capacity during concurrent submission.

A capture reads current source data through the existing repeatable-read valuation algorithm. Only a successfully validated result is published atomically. COMPLETE and UNAVAILABLE results are saved; missing or invalid sources can fail without creating a snapshot. Replay works at full capacity. The frontend retains an unconfirmed request and its exact payload in localStorage; explicit retry reuses the same UUID, including after refresh. Confirmed validation/missing/capacity rejection clears the pending request. It does not silently retry writes.

GET scenarios and per-scenario snapshots support 1..20 item keyset pages with opaque cursor; snapshot status may be COMPLETE or UNAVAILABLE. Historical detail rechecks scenario hash, report hash, frozen membership/limits, and full offline reproduction before returning the envelope. It does not query current market prices or account state. Summaries preserve unavailable totals as null, never zero.

Download contains history IDs, frozen scenario, original report and every valuation input. Run:

```sh
uv run --frozen python -m scripts.verify_portfolio_history export.json
```

The verifier requires canonical UUIDs, aware timestamps, scenario-before-snapshot ordering, no report after storage time, exact envelope shape and matching reproduced report. The CLI bounds file size to 32 MiB. It establishes internal consistency, not source provenance. The original `scripts.verify_portfolio` continues to accept direct valuation exports.

No historic as_of input is accepted. Timestamps are UTC. Risk limits generate hints only. No orders, account cash, ledger revisions or controls are changed. No continuous performance curve, drawdown, shared-capital portfolio, full OMS/Risk, or Live trading is implemented.
