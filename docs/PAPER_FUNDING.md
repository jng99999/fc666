# Account-scoped projected funding v1

paper-projected-funding-v1 integrates durable reservations with the production closed-bar Paper preparation/consumption path using PostgreSQL only. SQLite fault/submission laboratories remain isolated; there is no cross-database dual write. Existing spot-paper-closed-close-v1 formulas, source gates, receipts and account snapshots retain their original meaning. Live remains disabled.

## Reservation scope

New preparations declare immutable funding_version at schema0014. In the first independently committed preparation transaction, original authorization and lifecycle creation precede a funding reservation. Reservation reconstructs exact chronological projected orders, their authorization hashes, before/after cash/inventory and required per-order resources. BUY requires quantity*price+fee cash; SELL requires inventory and credits proceeds minus fee. Denied orders require zero resources; consumed batches without fills explicitly report no economic trade effects. Every balance transition must match the original projected fill receipt; all balances remain nonnegative.

A batch freezes up to10 observations. Reservations are the maximum net cash/inventory draw from initial balances over the chronological projected path, not the sum of all buys/sells. Earlier projected credits fund later projected debits only within the same indivisible consumption transaction; they do not become spendable external capital while pending. This is an account-specific projected-fill reservation, not a full strategy demand/requested quantity, limit order, exchange order or shared-capital reservation. Partial-cancelled original demand is not reconstructed.

Cash/inventory ledger balances remain unchanged during preparation. Read-only available quantities subtract the active reservation. Account row locking, frozen base revision/digests and one pending preparation per account serialize the production path; stale base cannot accept the batch. No additional withdrawal, transfer or asynchronous order endpoint is introduced. Local laboratory owner labels/tokens are not silently promoted into product authorization.

## Settlement and rollback

Before accepting economic effects, consume verifies declared reservation against frozen original authorization/ledger. After accepting original observations and matching actual order/fill receipts, it compares exact actual account balances and complete ledger digest with reservation projections. The immutable funding outcome, lifecycle outcome, accepted original ledger/completed intents and preparation terminal state commit in the same PostgreSQL transaction. A failure at any point rolls all effects back; the independent original reservation remains pending.

Control, expiry and source cancellation record a zero-economic-effect funding outcome, release the logical hold and preserve original reservation. Release means the hold stops reducing available funds; it is not another cash/inventory credit. Original partial fills retain their existing PARTIAL_CANCELLED outcome and consume only their projected actual quantity/fee; unmodeled strategy demand is never reserved or released as real inventory.

Funding rows reject UPDATE/DELETE; preparation funding_version cannot change. Declared missing/corrupt/rehashed-wrong evidence and missing terminal outcomes fail closed without repairing or cancelling as ordinary valid evidence. Pending legacy preparations with funding_versionNULL continue the original compatible path and never acquire invented historical reserves; available/reserved resources are unknown (null) while such a batch is pending. Terminal legacy coverage stays LEGACY_UNAVAILABLE. Existing versioned exports remain valid.

## Read-only inspection

GET /api/v1/paper/streams/{id}/funding uses a repeatable-read snapshot, verifies latest20 complete preparation batches and reports total_batches/window_limit/has_older. Maximum1000 retained preparations and32MiB response. It verifies actual consumed observations and receipts against account history, rather than trusting a terminal marker. A pending reservation outside the bounded window fails instead of silently omitting it. More distant history is outside this inspection.

The realtime page shows active holds, available cash/inventory, original batch reserves, settlement and legacy coverage. Failed refresh clears prior confirmation. Reads never execute, repair, replay or mutate accounts. Funding creation clocks must follow the original lifecycle coverage; losing that coverage cannot be repaired by enrolling a new creation after reservation. Existing stopped accounts remain stopped.

## Acceptance boundary and next work

Tests use isolated PostgreSQL only: exact buy fees and partial quantity, inventory sells, denied orders, independent accounts, cancel/expiry/source release, immutable tables, after-insert rollback, concurrent lost-reply consumption, corrupted/missing/rehashed-wrong evidence, legacy null availability and latest20 scope. Real browser checks use an existing stopped account and do not synthesize product market data or accounts.

This is the first production account-level economic integration. It does not integrate laboratory asynchronous private-style fills into the old engine, reserve full strategy demand or provide exchange ACKs, shared capital, multiuser permission, production lease ownership or live execution. Those require a new engine/request contract and separate verification. Next: versioned explicit requested-quantity execution contract for partial/late fills with capital reservation and atomic economic ledger updates, preserving all old engine snapshots.
