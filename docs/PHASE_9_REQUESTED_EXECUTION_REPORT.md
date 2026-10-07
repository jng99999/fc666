# Explicit requested-quantity contract acceptance

Implemented an independent versioned economic reducer, bounded hashed exports and an offline CLI. The full requested quantity is explicit and funded before submission. Partial and late fills settle exact cash, inventory, basis, fees and realized PnL. Cancellation acknowledgement keeps the unfilled reservation; a matching local source seal releases it without crediting cash or inventing fills.

Validation in this slice:

- 117 tests passed across requested execution (initial 37), cumulative reconciliation, persisted fault adapter, fenced submission and PostgreSQL projected funding; 117.36 seconds. One existing Starlette/httpx deprecation warning.
- Final requested-execution suite: 40 tests passed, including three additional component-fill, protective-floor/rejection and existing-basis cases. Thus 120 distinct tests passed across the selected suites.
- Fixtures cover duplicate/conflicting deliveries, incomplete source prefixes, unknown submission, partial cancellation, late fills, receipt invalidation, source sealing, exact fees and basis rounding, export tampering, numeric bounds and CLI success/failure. Fixture records never enter product account storage.
- Frozen Alembic check found no new upgrade operations. Schema remains 0014.
- API, web, research, market and paper service readiness passed. No service restart or frontend build was needed for this isolated Python contract. No fresh full-repository test run is claimed.
- Whitespace validation passed. No dependency, environment, credentials or startup changes.

This is a pure contract and offline verifier, not a deployed asynchronous trading engine. Existing PostgreSQL funding and closed-bar engines retain their formulas and persistence boundaries. Local source sealing does not establish venue finality or authenticate exported evidence. Live execution remains disabled.

Next goal: a separately versioned PostgreSQL request/event journal with atomic reservation and ledger settlement, concurrent ownership and transaction-interruption acceptance, preserving all earlier account histories.
