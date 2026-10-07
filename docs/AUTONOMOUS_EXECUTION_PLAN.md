# FC666 execution goals and acceptance gates

The user authorizes continued implementation, dependency installation, testing, routine commits and branch pushes without repeated approval. Keep the current checkout and branch; preserve main, prior records, local secrets, proxy and CA. Execution proceeds through concrete verified slices. A passing gate requires actual checks; authorization never substitutes for verification. Environment publication is performed through product settings; draft saving is not publication.

## Goal 1 — trustworthy persisted observations

Deliver immutable named scenarios, idempotent snapshots, exact offline replay, bounded history queries and recovery after lost responses. Completed in 0dd314e, with 248 tests and real-browser verification. Five owned services restarted; source trading remains disabled.

## Goal 2 — explain comparable history

Implemented; acceptance evidence is in PHASE_9_CONTINUITY_REPORT.md.

Deliver a versioned, bounded analysis using only saved inputs, deterministic intervals and segment breaks, missing-observation counts, absolute Decimal equity changes, a clear UI and full offline reproduction. Preserve unavailable values as null. Same-minute records, nonadvancing clocks, source/account changes and worker warnings must be explicit breaks. No interpolation or cross-gap cumulative analytics. Gate: hand-calculated cases, real PostgreSQL/API checks, export tampering rejection, unchanged original snapshots, window-boundary tests, real browser and existing history/valuation regression, typecheck/build/migration consistency and service restart.

## Goal 3 — sampled visual analysis

Implemented as sampled-v1; see PORTFOLIO_SAMPLED.md and PHASE_9_SAMPLED_REPORT.md for scope and acceptance.

Next slice after Goal 2 passes: specify segment-scoped sampled equity display and metric denominators before implementation. Clearly distinguish observed-point drawdown from actual intraminute/continuous drawdown. Do not join segments, fill missing minutes, annualize sparse points, or infer results outside the bounded window. Gate: independent hand calculation, gap/zero-equity/duplicate cases, exact reproducible exports and browser labels.

## Goal 4 — portfolio risk observability

First hint-only policy/history slice implemented; see PORTFOLIO_RISK_HISTORY.md and PHASE_9_RISK_REPORT.md. Full risk observability beyond this limited policy remains incomplete.

Freeze risk policy versions and expose read-only breach histories with known account/rule/price clocks, degraded state and explicit scope. Preserve current hint-only behavior until a separate execution gate is specified and validated. Gate: correlated assets and duplicate account exclusion, Decimal arithmetic, stale/missing inputs, worker outage and state restoration. Full risk monitoring is not implied by a gross-weight hint.

## Goal 5 — execution and recovery foundations

First read-only recovery-inspection and transaction fault-acceptance slice implemented. See PAPER_RECOVERY.md and PHASE_9_RECOVERY_REPORT.md. Independent immutable completed Paper intents now persist atomically with accepted bars, with explicit terminal paths and legacy coverage; see PAPER_ORDER_INTENTS.md. Two-phase closed-bar preparation now commits PREPARED independently before accepting the ledger, with recovery gates and cancellation; see PAPER_PREPARATION.md. This prepares a simulation batch, not a quantity-sized exchange order. Explicit per-order pre-execution authorization and a full exchange execution state machine remain next work.

Specify order lifecycle, idempotent intent, acknowledgement loss, reconciliation, restart safety and per-account entry limits. Implement and fault-test these in Paper before evaluating exchange execution. Shared capital, multiuser authentication, live credentials, production rollout and real order placement remain separate incomplete goals. Do not represent absent integrations as working. Automated capture requires a separate capacity/retention policy before enabling continuous scheduling: current immutable storage caps are 100 scenarios and 200 snapshots.

## Delivery rule for every goal

Read current state; implement one coherent reviewable slice; verify meaningful backend/database/browser behavior; stop only owned services for production build; restore and functionally check; document outcomes and outstanding scope; save tested startup changes when needed; commit and push the feature branch. Investigate failures and fix causes before declaring completion. No extra approval is needed for authorized routine work. Fresh-task restoration and environment publication are reported only with actual evidence.
