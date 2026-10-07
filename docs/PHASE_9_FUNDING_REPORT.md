# Phase 9 — production projected funding integration

New PostgreSQL reservations now commit independently with actual closed-bar preparation, before economic acceptance. Original account cash/inventory/fees, accepted observations, completed receipts, lifecycle, immutable funding settlement and preparation outcome commit atomically. Cancellation releases the hold without creating a cash/inventory credit. Production schema is0014.

This integrates projected-fill funding into real production Paper paths, using account row locks, frozen base revision/digests, original source/time gates and one pending preparation. It does not dual-write the SQLite submission laboratory into PostgreSQL, reinterpret an old snapshot or attach synthetic laboratory fills to product accounts. Full requested quantity and asynchronous execution require a separately versioned engine.

Scope is ACCOUNT_SEQUENTIAL_PROJECTED_FILLS: chronological exact authorization quantities/prices/fees, maximum net cash/inventory draw through the atomic batch, complete original projected ledger digest and actual effect comparison. Denied orders reserve nothing. Partial-cancelled strategy demand is not invented. Old funding_versionNULL history remains explicitly unavailable; pending legacy resource availability is null. Declared missing/corrupt evidence fails closed, even if an attacker recomputes a changed payload digest.

Read-only API and realtime widget show current holds/available resources, latest20 batch reservations/settlements and explicit older/legacy scope. Reads do not execute, repair or change accounts; a failed refresh clears confirmation. Bounds remain1000 retained preparations and32MiB response. No additional dependencies, secrets, network destinations or services.

Verified acceptance:

- Full scripts/check.sh:468 tests passed, Alembic no new operations, TypeScript and production build passed. Full backend suite took1338.56 seconds; this is functional verification, not load/latency acceptance.
- Final22 funding tests passed after clock, no-fill display and original-creation coverage strengthening. Covers exact buy fees, partial quantities, inventory sells, denied/no-fill outcomes, independent accounts, source/expiry/control release, immutability, prepare/consume insert rollback, concurrent committed-response loss, corrupted/missing/rehashed-wrong evidence, legacy null resources, latest20 range and refusal to recreate missing original creation. Slowest final test phases were isolated database teardown, about5.14 seconds. One existing Starlette/httpx deprecation warning remains.
- Existing preparation/authorization/lifecycle focused regression:71 passed.
- Main schema upgraded0014. scripts/start.sh restored API/web/research/market/paper; all functionally ready, trading disabled.
- Real stopped-account browser tests.browser_paper_recovery passed: funding range/read-only/failure clearing plus previous inspection widgets, unchanged account,393px layout, refresh and no JavaScript errors. This legacy account does not demonstrate new nonempty funding reservations; isolated PostgreSQL tests verify those.
- Existing offline intent export verifies LEGACY_UNMATERIALIZED; recovery verifies TERMINAL_RETAINED with one original order/fill. No standalone offline funding verifier is claimed.

Frontend type check/build and service restoration completed; current-instance verification is reported separately from environment configuration publication. Startup script preserves .env/volumes and upgrades0014. Existing main, checkout, proxy/CA and runtime files are retained; no extra worktree.

Next: new explicit requested-quantity execution contract with reservation, partial/late fills and cancel reconciliation, then production ownership and shared capital. Full OMS, private exchange calls, identity authorization and Live remain incomplete.
