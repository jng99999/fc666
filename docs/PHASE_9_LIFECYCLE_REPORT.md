# Phase 9 — independent local Paper lifecycle acceptance

Implemented paper-local-lifecycle-v1 at schema0013. Immutable coverage and CREATED events commit with pre-execution preparation; local FILL/OUTCOME evidence commits with original accepted ledger and completed receipts. Read-only repeatable-read inspection verifies complete authorization/evidence, actual consumed receipts and cumulative Decimal quantities, fees and notional. Missing declared evidence fails closed, with no automatic repair or replay.

Legacy pending preparations with supported authorization may enroll before consumption in a separate preparation transaction. Legacy terminal preparations remain LEGACY_UNAVAILABLE. Latest20 complete batches have explicit total/window/older scope, a1000 retained-preparation cap and32MiB bound. Inspection never changes accounts.

## Verified acceptance

- Full scripts/check.sh:389 Python tests passed; Alembic reports no new operations; TypeScript and production build passed.
-25 dedicated lifecycle tests cover first durable creation, original filled/partial/rejected receipts, source/expiry/control cancellation, duplicate/conflicting/reordered events, gaps/overquantity, immutable rows, transaction rollback, concurrent consumption/lost response, legacy enrollment and bounded API behavior.
- Existing preparation/authorization focused regression:46 passed.
- Main database upgraded0013; scripts/start.sh restored API/web/research/market/paper, all ready with trading disabled.
- Real browser tests.browser_paper_recovery: existing stopped account with a real public-source simulated fill, read-only lifecycle scope, failed-refresh clearing, account unchanged,393px layout, refresh and no JavaScript errors. This old account does not demonstrate new nonempty lifecycle coverage; isolated database tests verify it.
- Existing offline intent export verifies LEGACY_UNMATERIALIZED; recovery export verifies TERMINAL_RETAINED with one original order/fill. No standalone offline lifecycle verifier is claimed.

No additional dependencies, credentials, hosts or services. Existing checkout/main, .env, volumes, proxy and CA preserved. Environment startup configuration is draft-only, not publication or evidence of fresh-task restoration.

## Scope and next goal

Production creates one local fill per authorization, with sequences0..2. Split-fill pure-reducer fixtures verify arithmetic and deduplication only; they do not implement a private exchange adapter. Post-terminal new events require separate reconciliation and fail closed here. Real cancel/fill races, exchange ACKs, private callbacks, full OMS, identity authorization, shared capital and Live remain incomplete.

Next: specify a versioned Paper-only fault adapter and cumulative private-style reconciliation contract, including late fills around cancellation, unknown submission and restart/lost-response cases before any exchange integration.
