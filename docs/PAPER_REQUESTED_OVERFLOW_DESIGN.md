# Requested Paper settlement overflow and archival design

Status: bounded local STAGED_UNAPPLIED inbox is implemented at schema0025; see FIVE_BLOCK_FOUNDATIONS.md. Segmented application, archival deletion, rollover and external transport remain proposed and unimplemented. A bounded journal cannot guarantee retention of an arbitrarily long sequence of partial fills. Removing or merging historical rows is not an acceptable way to make capacity available.

## Immediate implemented protection

New writes and current event previews refuse a repeated ACK or repeated cumulative RECEIPT if it would leave fewer event slots than the shortest terminal path. Both account and request limits apply. A repeated ACK means an ACK already appears in this request's history; a repeated RECEIPT means its pre-event summary already confirms the current unique-fill totals. Repeated information may still be accepted while enough slots remain. A remaining full fill plus SEAL needs2 slots; full-fill/rejected/cancellation-result evidence needs1 SEAL.

This rule does not filter first acknowledgements/receipts, new fills, UNKNOWN_SUBMISSION, cancellation requests/results, REJECT or SEAL. Those facts retain existing event validation and hard limits. A distinct repeated FILL event is also left to the existing fill ledger and hard limits; deduplication must not invent sequence continuity. Exact event/source/owned retries run before this new policy, and persisted historical preview/receipt replay is unchanged. Current previews can refuse a proposal that remains valid under frozen preview-v1 replay. No refused input is reported committed or persisted. Callers must retain refused inputs; an HTTP409 is not durable ingestion.

This is a narrow mitigation, not settlement overflow support. First observations, new fills or safety state changes can still consume the final slots. Byte budgets and other evidence budgets can fail before event counts. The policy does not promise a terminal fill/rejection/cancel fact or command an external cancellation. Existing UNKNOWN/cancel/partial holds and stopped-account late economic events remain meaningful.

## Required durable overflow inbox

Introduce a separately versioned append-only inbound envelope and explicit STAGED_UNAPPLIED outcome. It must identify account, immutable request, source, original source sequence/event identity, body digest and server receipt checkpoint. No client label establishes exchange provenance. Local operator grants and owned delivery fences must be enforced before reading/staging data. A conflicting duplicate must fail; an exact retry returns original staging evidence without a new row or extending authority. Acceptance of a staged envelope must never set event_committed=true, advance financial revision, acknowledge settlement or release reserved funds.

Budget and validate the envelope before loading or writing it. Bound count, per-envelope bytes and aggregate bytes independently, with observed capacity diagnostics. A full/unavailable inbox must return an explicit nonaccepted result; neither silently drop data nor claim guaranteed retention. A production transport needs its own durable upstream spool and backpressure contract before this inbox becomes a reliable ingestion boundary. Evidence of denied/stale ownership must not be relabeled as an authorized delivery.

## Required segmented replay contract

Freeze existing journal-v1/v2 and event/source/ownership/control/health contracts. A new journal version must define ordered segment identities, contiguous global source sequences, global duplicate event/fill identities, financial revision continuity and immutable predecessor hashes. The current1000-event bound is embedded in validation and sequence grammar; changing one constant or a database query cannot safely support overflow.

Each archival segment must contain enough immutable financial and related evidence to reproduce the state at its boundary. Manifest proofs must bind opening/checkpoint, segment range, complete event and unique fill ledger, risk/health receipts, source declarations/receipts, ownership/fence checkpoints, dispatch/assessment references and pool admission context. A hash verifies consistency, not source authenticity. References to omitted or inaccessible segments must fail full audit and command admission. A recent-window summary cannot substitute for the complete historical ledger.

Keep each decode/replay unit bounded and expose explicit manifest/chunk export versions. Offline verification must reject missing, duplicated, reordered, truncated and resealed wrongly bound segments; preserve global deduplication across boundaries. Snapshot/checkpoint computations cannot reset cash, inventory, cost basis, fees, realized PnL, UNKNOWN state or funding holds. Full state replay and immutable checkpoint agreement must be checked before new writes.

## Commit, recovery and retention order

1. Lock account and any owning pool/all members in the established order; freeze the audited revision and segment boundary.
2. Persist the immutable complete segment and manifest on a supported durable store; verify exact bytes/checksum and replay. Storage failure leaves original data and funds unchanged.
3. Atomically commit the new segment reference/checkpoint and, separately, an application receipt for staged events under their current valid scope/fence. Apply events only in contiguous sequence order. Roll back event/source/health/dispatch/financial changes together on failure.
4. Demonstrate SIGKILL before commit and lost-reply recovery after commit: one manifest, one applied receipt and one financial effect. Database/archive restart or temporary unavailability must refuse further mutation without destroying prior evidence.
5. Only after backup/restore and manifest reachability acceptance may a separately authorized retention operation remove redundant hot rows. Immutable source data remains recoverable. Empty/populated downgrade behavior must not discard historical evidence.

Closed-account rollover is a separate design, not an escape hatch. It requires sealed/void terminal history, no active reservation, financial totals carried exactly and explicit pool membership/transfer semantics. Current immutable pool allocations cannot simply be moved to a new account ID. Late external facts after a local seal require a new reconciliation contract; SEAL is not evidence of authenticated exchange finality.

## Acceptance gates before enabling scheduling or transport

The implementation must prove both account and per-request exhaustion, byte exhaustion, multiple partial fills, duplicate/redelivery after segment boundaries, missing archive segments, stale/revoked authority, STOP with late fills, pool-wide atomicity, crash/reopen/lost-reply recovery and archival restore. Main operator grants and Live remain disabled while these gates are unmet. No fixture balances or market envelopes enter the main database.

Implemented prerequisites now include the immutable bounded inbox and atomic contiguous drain. `core/paper/requested_archive.py` additionally transports a complete frozen financial journal export in canonical ASCII chunks of at most 64 KiB, with contiguous offsets, predecessor hashes and an aggregate manifest. Verification reconstructs the complete export and independently replays all financial events, including pending holds. This financial-only artifact excludes control, health, ownership, source, dispatch and pool evidence; it cannot authorize archival retention, restore a complete account operational state, or raise the 1000-event bound.

Capture with `.venv/bin/python -m scripts.paper_archive capture --account-id ACCOUNT --output /protected/new-archive.json`; verify offline with `.venv/bin/python -m scripts.paper_archive verify --input /protected/new-archive.json`. Capture audits the current database journal and performs no financial writes. Output uses exclusive creation, mode0600 and file fsync; an interrupted file must pass full verification before use. Hashes establish internal consistency, not trusted provenance. Keep the original complete backup and hot history.

Next implementation boundary is a versioned complete operational evidence manifest with coherent financial/control/source/ownership/health/pool checkpoints, followed by segmented application and crash/restore acceptance. Segmented journal application and archival retention remain unimplemented.

The optional `capture --operational` mode now captures a versioned financial/control/source/ownership/dispatch/health evidence bundle under one account lock and transaction. It binds identical journal and control exports across dispatch and health evidence, independently replays each existing contract, then uses a distinct segmented operational archive version. Aggregate evidence remains bounded to32MiB; oversized exports fail instead of dropping sections. Attempts, inbox and pool evidence are explicitly excluded, so this is not a complete operational restore or a retention authorization. `verify` selects the contract by version and rejects mixed checkpoints or attempts to expand authority by resealing the manifest. No historical v1 financial archive format is changed.
