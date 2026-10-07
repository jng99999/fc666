# Controlled explicit-quantity Paper acceptance

Implemented explicit opt-in policy enrollment, immutable account controls, full-request risk gates and a separate read-only export/offline verifier at schema0016. Admission evidence commits with preparation and first local SUBMIT under the account lock. Control revision is separate from financial revision. Existing un-enrolled v1 journals remain LEGACY_UNMANAGED, and prior closed-bar account engines remain separate.

## Executed validation

- Corrected parent-request/gate FK flush ordering within the same transaction. The interrupted exploratory run failed on that ordering; it is superseded by the passing checks below. No commit occurred from failed transactions.
- After the correction, initial24 controlled-account tests passed in130.96 seconds.
- Expanded119 selected tests passed in446.12 seconds:31 control cases,21 old journal cases,32 complete financial inspection cases,12 API/timeout cases, migration roundtrip/base/head and22 existing PostgreSQL funding cases.
- Final nine additional cases passed in11.40 seconds: unsubmitted STOP hold retention, seven invalid explicit-policy inputs and marker/reenrollment protection. Thus128 distinct selected tests passed; the final controlled suite comprises40 cases. One existing Starlette/httpx deprecation warning remains. No fresh full-repository suite is claimed.
- New policy limits independently deny excessive full quantity, requested notional, BUY inventory, fee ceiling, cash-floor depletion and disallowed side, with unchanged request/financial/control exports.
- Enrollment starts PAUSED without changing balances or financial revision. Pause between preparation and SUBMIT denies dispatch; fresh resume permits it. HALT denies BUY and permits allowed reducing SELL. STOP cannot resume. Exact retries retain one effect; stale concurrent controls cannot both commit.
- After PAUSE or STOP, previously submitted partial/late fills settle exact balances and retain remaining cash until local source sealing. Exact old SUBMIT redelivery after stop creates no new dispatch. Stopping an unsubmitted prepared request retains its full hold and denies dispatch; local-void termination is not implemented.
- Last retained control slot is reserved for STOP. Policy/control/gate UPDATE and DELETE protections and one-way enrollment marker are tested. Missing declared gates block reads, SUBMIT and both inspection APIs without partial reports.
- Exceptions after enrollment/control/gate INSERT roll back marker, request, gate and economic changes. Actual subprocess SIGKILL before command/gate commit rolls back; after commit/lost reply, new connections and exact retry recover one durable result. These are process/transaction tests, not infrastructure-failure acceptance.
- GET-only controls API,404/no-store,405 mutation rejection and unchanged financial-v1 export are tested over real isolated PostgreSQL. Offline controls CLI succeeds with an unusable database URL; altered/rehashed admission decisions fail replay.
- Migration roundtrip and frozen Alembic check passed. Main head is0016; all six requested-engine tables remain empty. No fixture balance or fabricated trade enters product records.
- Only owned services stopped; scripts/start.sh preserved .env/volumes, migrated head and restored all five services ready with trading disabled. Running controls OpenAPI GET-only and missing-account404/no-store smoke passed. No frontend changes/build/browser claim or dependency/credential/domain/additional-service change.
- Existing start_skill preserved and appended with tested0016 instructions; draft persistence confirmed. This is not publication or fresh-task restoration. Review/save/publish occurs in environment settings.

## Remaining scope

These are local controlled-account library gates, not coverage for every legacy Paper account, human permission checks, shared capital or private transport. Risk limits use requested limit notional and inventory units; they do not implement marked exposure, drawdown or source-health/freshness risk. Denials are not retained attempt history. Checksums establish internal consistency, not external authenticity or finality. No mutation API/UI or scheduler is opened; Live remains disabled.

Next: a separately versioned explicit terminal protocol for an unsubmitted request, preserving held capital and source uncertainty without invented SUBMIT/cancellation evidence, before mutation endpoints or scheduling.
