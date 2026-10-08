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

First read-only recovery-inspection and transaction fault-acceptance slice implemented. See PAPER_RECOVERY.md and PHASE_9_RECOVERY_REPORT.md. Independent immutable completed Paper intents now persist atomically with accepted bars, with explicit terminal paths and legacy coverage; see PAPER_ORDER_INTENTS.md. Two-phase closed-bar preparation now commits PREPARED independently before accepting the ledger, with recovery gates and cancellation; see PAPER_PREPARATION.md. This prepares a simulation batch, not a quantity-sized exchange order. Explicit immutable per-order local Paper rule authorization and derived account direction gates are now saved with preparation and recomputed before acceptance; see PAPER_AUTHORIZATION.md. Independent local CREATED/FILL/OUTCOME lifecycle evidence and cumulative quantity/fee/notional verification are now implemented; see PAPER_LIFECYCLE.md and PHASE_9_LIFECYCLE_REPORT.md. A full exchange execution state machine, identity authorization, shared capital and private reconciliation remain next work.

Specify order lifecycle, idempotent intent, acknowledgement loss, reconciliation, restart safety and per-account entry limits. Implement and fault-test these in Paper before evaluating exchange execution. Shared capital, multiuser authentication, live credentials, production rollout and real order placement remain separate incomplete goals. Do not represent absent integrations as working. Automated capture requires a separate capacity/retention policy before enabling continuous scheduling: current immutable storage caps are 100 scenarios and 200 snapshots.

## Delivery rule for every goal

Read current state; implement one coherent reviewable slice; verify meaningful backend/database/browser behavior; stop only owned services for production build; restore and functionally check; document outcomes and outstanding scope; save tested startup changes when needed; commit and push the feature branch. Investigate failures and fix causes before declaring completion. No extra approval is needed for authorized routine work. Fresh-task restoration and environment publication are reported only with actual evidence.


## Continuing mandate and remaining acceptance goals

The user explicitly renews ongoing autonomous execution: do not request routine confirmation between goals. Continue implementation, necessary installation, meaningful checks, fixes, commits and feature-branch pushes. Report actual progress and blockers. A finite chat turn is not an unattended scheduler; do not claim background execution after the turn ends.

6. Paper reconciliation: bounded, versioned cumulative fill/cancel transcripts; duplicates, source ordering, late fills, unknown submission and exact fees/notional. Start with an isolated pure contract, then durable fault-adapter evidence and restart acceptance. No product ledger mutation from fixture transcripts.
7. OMS: independently durable requested quantity, stable submission identity, unknown outcome, cancellation, fencing and reconciled terminal state. Gate: transactional faults, concurrent ownership, lost replies and process restart.
8. Execution risk: shared-capital ledger and atomic reservations, aggregate exposure, freshness/health gates and kill switch. Gate: concurrent accounts and denied side effects.
9. Private connectors and identity: typed capabilities, protected credentials, permission isolation, balance/orders/fills reconciliation. Test without real order placement first.
10. Production acceptance: operational monitoring, backups/restore, long-load and infrastructure faults, deployment and rollback. Live readiness requires concrete completed gates and actual account/risk configuration.
11. Research expansion: strategy SDK/sandbox, additional matching and data coverage, optimization, then reproducible AI intelligence. Each ships with explicit unsupported capabilities and original-data provenance.

Next work is chosen automatically from dependencies and verified failures. Passing a test gate requires evidence; broad authorization is not a substitute for passing it.


### Goal6 progress — durable isolated fault laboratory

Pure cumulative contract and dedicated immutable SQLite request/event/evidence storage implemented; see PAPER_RECONCILIATION.md. Genuine process SIGKILL before commit rolls back; after commit/lost reply retains one result under reopen/retry. Full-capacity duplicates, concurrent deliveries and corrupted/missing evidence fail safely. This lab has no product economic effects, API/UI or external transport. Production schema remains0013. Next dependency: durable simulated submission ownership/fencing and query-before-retry for unknown outcomes, then a separately versioned account/economic integration. Full OMS remains incomplete.

### Goal7 progress — fenced simulated submission

Isolated local leases, fencing tokens, one durable simulated dispatch, immutable initial-event evidence and query-only unknown-result recovery implemented; see PAPER_SUBMISSION.md and PHASE_9_SUBMISSION_REPORT.md. Actual process interruption, old owner rejection, concurrent selection/submission and expiry rollback are verified. This does not connect submission to product capital or exchange transport. Next: versioned economic integration contract including explicit requested quantity, reservation/authorization, partial and late fills and cancellation; preserve all original engine versions. Full OMS remains incomplete.

### Goal7/8 progress — production projected funding

New PostgreSQL preparation reservations and atomic account settlement integrate projected-fill funding into the existing real closed-bar Paper path at schema0014; see PAPER_FUNDING.md and PHASE_9_FUNDING_REPORT.md. No SQLite/PostgreSQL dual write. Account row locking, base revision/source/time gates and one pending batch remain the production fencing boundary. Local leased fault adapter remains separate. Next: a new explicit requested-quantity engine contract supporting asynchronous partial/late fills and capital reservations; preserve existing snapshots/formulas. Full OMS and shared-capital execution risk remain incomplete.

### Goal7/8 progress — explicit quantity economic contract

The independent `paper-requested-execution-v1` contract now freezes and funds the full requested quantity, folds asynchronous partial and late fills, retains reserves after cancellation acknowledgement and releases only after a local source seal. Exact offline replay and hand-calculated ledger tests are implemented; see PAPER_REQUESTED_EXECUTION.md and PHASE_9_REQUESTED_EXECUTION_REPORT.md. This does not yet mutate product accounts. Next: a separately versioned PostgreSQL request/event journal with atomic reservation, fill acceptance and settlement, transactional interruption and concurrent ownership tests. Existing engine records and schema0014 remain intact.

### Goal7/8 progress — PostgreSQL explicit-quantity journal

Independent local Paper opening evidence, requested-quantity reservations and immutable source-event settlement now persist atomically in PostgreSQL at schema0015; see PAPER_REQUESTED_JOURNAL.md and PHASE_9_REQUESTED_JOURNAL_REPORT.md. Account row locks serialize concurrent requests and duplicate fills. The new namespace preserves prior closed-bar account formulas and histories. Next: bounded read-only journal inspection/API and complete offline verification, followed by explicit control/risk gates before exposing commands or scheduling. Shared capital, external dispatch ownership, private reconciliation and Live remain incomplete.

### Goal7 progress — complete journal inspection and offline replay

The independent requested-quantity PostgreSQL engine now has a read-only complete single-account export API and bounded offline verifier; see PAPER_REQUESTED_INSPECTION.md and PHASE_9_REQUESTED_INSPECTION_REPORT.md. Reconstruction verifies chained bases, clocks, identities, every request settlement and final account/revision; immutable database prefix evidence is verified before export. No mutation API/UI or scheduler. Next: immutable versioned account controls and explicit execution-risk gates before commands or continuous scheduling. Schema stays0015; shared capital, external ownership/private reconciliation/human identity and Live remain incomplete.

### Goal7/8 progress — controlled-account admission

Explicit opt-in immutable policies, PAUSED/ACTIVE/HALTED/STOPPED controls and PREPARE/SUBMIT risk evidence now persist atomically at schema0016; see PAPER_REQUESTED_CONTROLS.md and PHASE_9_REQUESTED_CONTROLS_REPORT.md. Commands share the economic account lock; control and financial revisions remain separate. Late receipts continue after pause/stop and retain cancellation holds until local source sealing. Un-enrolled v1 accounts remain explicitly LEGACY_UNMANAGED. Next: a separately versioned local finalization protocol for an unsubmitted request, before mutation endpoints or scheduling. Shared capital, health/identity gates and private reconciliation remain incomplete; Live stays disabled.

### Goal7/8 progress — safe unsubmitted finalization

Schema0017 adds immutable local VOID_UNSUBMITTED evidence and atomic release only for a request with zero durable source events; see PAPER_REQUESTED_FINALIZATION.md and PHASE_9_REQUESTED_FINALIZATION_REPORT.md. Submitted/unknown outcomes retain their holds. Financial/control v2 exports replay local closures while old captures remain v1. Next: specify explicit permission, idempotency and fencing for controlled Paper mutation interfaces before exposing writes or scheduling. Identity/shared capital, private connectors and Live remain incomplete.

### Goal7/8 progress — explicit local control capability

A default-disabled local operator control-command endpoint now requires an explicit server token, exact account/action grants and both control/financial revisions under the account lock. See PAPER_REQUESTED_COMMANDS.md and PHASE_9_REQUESTED_COMMANDS_REPORT.md. This is single-operator access, not multiuser identity/audit, and grants no order/source write capability. Schema stays0017. Next: extend the boundary to local unsubmitted finalization using a separate explicit action grant; then account preparation/dispatch contracts. Live remains disabled.

### Goal7/8 progress — independently granted local finalization

The default-disabled finalization-commands route now requires exact account and VOID_UNSUBMITTED grants, explicit controlled coverage and the financial revision under the shared account lock. See PAPER_REQUESTED_FINALIZATION_API.md and PHASE_9_REQUESTED_FINALIZATION_API_REPORT.md. No source/venue cancellation evidence is invented. Main remains empty and disabled; schema0017 and all existing export versions remain unchanged. Next: explicit enrollment/preparation permission and funding-preview contracts before submission or source-ingestion interfaces. Multiuser actor identity, shared capital, exchange reconciliation and Live remain incomplete.

### Goal7/8 progress — scoped enrollment and auditable funding preview

Default-disabled ENROLL/PREVIEW_PREPARE grants now protect immutable policy registration and non-persisting full-quantity previews; see PAPER_REQUESTED_ONBOARDING.md and PHASE_9_REQUESTED_ONBOARDING_REPORT.md. Enrollment starts PAUSED and fences the financial checkpoint atomically; preview uses both revisions and the audited server base, with complete offline replay. Schema0017 unchanged. Next: separately granted atomic request preparation that rechecks both versions, policy and funding; a preview never authorizes submission. Account creation, shared capital, actor identity, private reconciliation and Live remain incomplete.

### Goal7/8 progress — atomic scoped preparation

PREPARE is an independent default-disabled grant. A new request re-evaluates the server preview, financial/control checkpoints and risk/funding inside the existing account transaction. Immutable financial/control prefixes reconstruct exact retries after later activity, without a second hold. See PAPER_REQUESTED_PREPARATION_API.md and PHASE_9_REQUESTED_PREPARATION_API_REPORT.md. Schema0017 and original versions remain unchanged. Next: source-provenance/ingestion contracts and unknown-outcome handling before submission transport; shared capital, actor identity and Live remain incomplete.

### Goal7 progress — scoped source input forecast

Independent PREVIEW_EVENT protects a read-only, complete offline-replayable local event forecast; see PAPER_REQUESTED_EVENT_PREVIEW.md and PHASE_9_REQUESTED_EVENT_PREVIEW_REPORT.md. Source labels explicitly identify proposed local input, never authenticated venue origin. Unknown outcomes keep holds; exact duplicates preserve current account/later request. No economic/provenance writes and schema0017 unchanged. Next: immutable local source receipt/provenance evidence and transaction fault acceptance before exposing ingestion. Venue authentication, ownership, private reconciliation and Live remain incomplete.


## Current head0018 — atomic local source evidence

Read PAPER_REQUESTED_SOURCES.md and PHASE_9_REQUESTED_SOURCES_REPORT.md. Independent default-disabled INGEST_EVENT atomically persists immutable local source receipts with event settlement, preserves unlabeled history and original retry acknowledgements, and exports complete offline-replayable source evidence. Current head0018 supersedes earlier head/absence-of-ingestion notes. Run bash scripts/start.sh; preserve .env/volumes and verify five services. Main remains empty/default-disabled. No dependencies, hosts, secrets or frontend changes. Next: controlled submission ownership and unknown-result recovery contracts; venue transport, shared capital, actor identity and Live remain incomplete.


## Local submission attribution and read-only recovery

Read PAPER_REQUESTED_RECOVERY.md and PHASE_9_REQUESTED_RECOVERY_REPORT.md. GET recovery and the offline verifier preserve original declared submission identity, current account state and unknown/cancel holds. Every action prohibits automatic submission/retry; ownership remains explicitly NOT_IMPLEMENTED. Schema0018, disabled operator defaults and existing startup are unchanged. Next: durable PostgreSQL owner claim/takeover/fencing before dispatch integration. SQLite laboratories remain isolated; Live stays disabled.


## Current head0019 — durable local ownership and event fencing

Read PAPER_REQUESTED_OWNERSHIP.md and PHASE_9_REQUESTED_OWNERSHIP_REPORT.md. Immutable PostgreSQL claim history, database-clock expiry, token takeover and fenced source settlement share the account transaction. Claimed requests reject unfenced supported writes; no retrospectively claimed submissions. GET ownership and offline verification inspect complete evidence; original recovery-v1 retains its frozen unsupported-ownership view. No ownership HTTP writes, private transport or scheduler yet. Restore bash scripts/start.sh, preserve .env/volumes/proxy/CA and verify five services/current schema0019. Main remains empty/default-disabled; Live stays off. Next: explicitly scoped ownership claim/delivery capabilities, then uniquely durable dispatch integration.


## Independently scoped local ownership commands — head0019 unchanged

Read PAPER_REQUESTED_OWNERSHIP_API.md and PHASE_9_REQUESTED_OWNERSHIP_API_REPORT.md. CLAIM_OWNERSHIP and DELIVER_OWNED_EVENT are independent default-disabled actions with exact account/bearer scope. Claims check both revisions and prior token; active exact retries preserve the original lease without renewal. Owned source acceptance preserves preview/risk/funding and current owner/token/expiry checks atomically. Main remains empty/default-disabled, no dependencies/hosts/credentials/frontend changes. Restore via existing startup and readiness checks. Next: uniquely durable local dispatch evidence and unknown-result query-before-retry before transport integration. Live remains disabled.


## Current head0020 — unique local dispatch and recovery evidence

Read PAPER_REQUESTED_DISPATCH.md and PHASE_9_REQUESTED_DISPATCH_REPORT.md. New owned SUBMIT commits exactly one immutable original-client/request/source/claim/fence record atomically with the event and account transaction. Older NULL events remain explicitly unavailable and cannot gain retrospective identity. GET dispatches and its verifier reconstruct complete evidence and deny all remote retries/queries. Head0020 supersedes prior schema notes; restore bash scripts/start.sh, preserve .env/volumes/proxy/CA and verify all five services. Main remains empty/default-disabled. No dependencies/hosts/credentials/frontend changes or real venue transport. Next: controlled request-scoped recovery by original client identity before transport. Live stays disabled.

## Original-client scoped local query — head0020 unchanged

QUERY_DISPATCH independently protects read-only dispatch-queries with exact account scope, original request/client identity and current lease checks before/after complete replay. Read PAPER_REQUESTED_DISPATCH_QUERY.md and PHASE_9_REQUESTED_DISPATCH_QUERY_REPORT.md. Takeover preserves original identity; unknown results retain holds and never authorize resubmission. Offline verification is available. Main remains empty/default-disabled and five services ready. Next: explicit local transport boundary and fault contracts before venue integration; Live disabled.

## Offline transport failure boundary — head0020 unchanged

Read PAPER_REQUESTED_TRANSPORT_BOUNDARY.md and PHASE_9_REQUESTED_TRANSPORT_BOUNDARY_REPORT.md. Complete original-client query evidence now supports offline classification of timeout/disconnect/empty/contradictory/unsupported transport failures. Every classification retains funding and forbids release/resubmission; no remote outcome is invented. Four acceptance/regression cases passed, including20 state/failure assessments. Existing startup configuration remains sufficient. Next: durable local transport-attempt evidence and transaction fault acceptance before any adapter. Venue transport and Live remain disabled.

## Current head0021 — durable local failure-assessment attempts

Read PAPER_REQUESTED_ATTEMPTS.md and PHASE_9_REQUESTED_ATTEMPTS_REPORT.md. The internal PostgreSQL library stores immutable original-client/current-lease/checkpoint-bound assessment receipts, with exact retry, expiry rollback and16-record/request bounds. remote_send_performed=false explicitly distinguishes local assessment from venue transport. Populated downgrade is blocked; empty roundtrip supported. Twenty-six distinct cases passed. Main remains empty/default-disabled and five services ready. Restore via bash scripts/start.sh, preserve .env/volumes/proxy/CA and check0021. Next: independently scoped local assessment commands/read exports and process-loss/locking acceptance. No real transport or Live.

## Scoped assessment commands/export and process-loss acceptance — head0021 unchanged

Read PAPER_REQUESTED_ATTEMPTS_API.md and PHASE_9_REQUESTED_ATTEMPTS_API_REPORT.md. Independent RECORD_ASSESSMENT/READ_ASSESSMENTS protect strict local commands and historical exports with exact account/bearer scope; main defaults remain disabled. Complete nested evidence replay and bounded offline verification are available. Fourteen cases passed, including true SIGKILL rollback, concurrent exact retries and lock timeout without partial evidence. Five services ready; no schema/dependency/frontend/network changes. Next: historical-prefix binding and stronger export completeness contracts before local adapters. Actual transport and Live remain disabled.

## Historical assessment lineage and trusted coverage — head0021 unchanged

Read PAPER_REQUESTED_HISTORY_BINDING.md and PHASE_9_REQUESTED_HISTORY_BINDING_REPORT.md. Historical export receipts now bind to current financial/control/source/ownership/dispatch prefixes; replay-valid alternative histories and future observation fail closed. Optional independently trusted ordered receipt hashes detect missing tails without inventing unsigned origin authentication. Ten distinct acceptance/regression cases passed after correcting an alternative-history test fixture. Main remains empty/default-disabled and five services ready. Next: stored receipt/retry lineage binding and bounded processing before local adapters. Real venue transport and Live remain disabled.

## Stored assessment lineage and bounded processing — head0021 unchanged

Read PAPER_REQUESTED_STORAGE_BINDING.md, PHASE_9_REQUESTED_STORAGE_BINDING_REPORT.md and PROJECT_PROGRESS.md. Record/retry/capture/export now bind stored receipts to current evidence. SQL checks32MiB serialized stored-request budget before ORM loading and after insert; overflow rolls back without partial evidence. Twenty-two distinct cases passed, five services ready and main remains empty/default-disabled. No schema/dependency/frontend/network changes. Current local evidence chain is ready for the next dependency: shared-capital reservation contract and cross-account admission before scheduling/private transport. Whole-platform completion cannot be inferred from slice/test counts; five major downstream work blocks remain. Live stays disabled.

## Goal8 progress — fixed-allocation shared quote-capital forecast

Read PAPER_SHARED_CAPITAL.md and PHASE_9_SHARED_CAPITAL_REPORT.md. Complete canonical member journals now support offline pool cash/BUY-hold aggregation and candidate reservation-cap forecasts without double counting allocations or borrowing unallocated funds. UNKNOWN/cancel holds persist; SELL shared inventory is explicitly unsupported. Sixty-six cases passed, including26 new capital cases. This is pure/read-only: no coherent capture, durable pool, exclusive membership, shared reservation, transfer, API or scheduler. Schema0021/main empty/default-disabled/five ready services unchanged. Next: durable immutable pool membership and coherent locked account capture before shared writes. Live stays disabled.

## Current head0022 — immutable capital pools and coherent member captures

Read PAPER_SHARED_CAPITAL_POOL.md and PHASE_9_SHARED_CAPITAL_POOL_REPORT.md. Immutable exclusive membership now persists for untouched local account openings; canonical pool/all-member locking produces a coherent audited quote forecast. Exact retry/concurrent overlap/lock timeout/missing evidence/rollback/downgrade/CLI acceptance passed:54 distinct cases. Restore bash scripts/start.sh and verify0022/five services; preserve .env/volumes/proxy/CA. Main remains empty/default-disabled. No pool HTTP grant or actual shared reservation; existing per-account writes do not enforce aggregate caps. Next: pool-aware atomic admission/reservation evidence before shared scheduling. Venue transport and Live disabled.

## Current head0023 — atomic pool-aware local BUY preparation

Read PAPER_SHARED_CAPITAL_ADMISSION.md and PHASE_9_SHARED_CAPITAL_ADMISSION_REPORT.md. Internal pool-first/all-account locking now commits local risk/aggregate-cap-approved requests, gates, holds and immutable mandatory admissions atomically. Ordinary new pooled preparations cannot bypass; every pooled journal audit requires complete admission evidence. Retry/fault/concurrency/migration/prefix/CLI acceptance verified:51 distinct cases including nine final admission cases and16 unpooled preparation regressions. Restore bash scripts/start.sh and verify0023/five services; preserve .env/volumes/proxy/CA and empty/default-disabled main. Upgrade refuses historical pooled requests lacking admission. Next: independently scoped pool-aware preview/preparation/read with whole-member disclosure permissions. No transfers/shared SELL/full risk/private transport or Live.

## Scoped pool preview/preparation/read — head0023 unchanged

Read PAPER_SHARED_CAPITAL_API.md and PHASE_9_SHARED_CAPITAL_API_REPORT.md. Independent PREVIEW_POOL_PREPARE/POOL_PREPARE/READ_POOL require explicit pool IDs and all member account grants before financial capture. Pool forecasts remain read-only; commands recheck current cap and local nested preview hash before atomic admission. No target-only cross-account disclosure. Twenty-five distinct acceptance/regression cases passed. Main remains empty/default-disabled, actual three routes503/no-store and five services ready. No schema/dependency/frontend/network changes. Next: pooled process-loss/control/retention acceptance before scheduling/full risk integration. No transfers/shared SELL/private transport or Live.

## Pooled command fault acceptance — schema0023 unchanged

Read PAPER_SHARED_CAPITAL_FAULTS.md and PHASE_9_SHARED_CAPITAL_FAULTS_REPORT.md. Genuine child SIGKILL/reopen proves precommit rollback and postcommit lost-reply recovery with one hold/receipt. Scaled request capacity, revoked whole-member scope and STOP boundaries preserve original evidence. Reproduced and fixed preparation with exhausted source history: preview and actual preparation now refuse new holds, preserving exact retry. Sixty-six acceptance/regression cases passed. Main fourteen related tables remain empty, operator grants/Live disabled, five services ready. Startup draft saved, publication remains separate. Next: explicit event-capacity lifecycle/headroom and freshness/health gates before scheduling; long-term retention, shared SELL/transfers/private transport/production remain incomplete.

## Initial structural event headroom — schema0023 unchanged

Read PAPER_REQUESTED_CAPACITY.md and PHASE_9_REQUESTED_CAPACITY_REPORT.md. New preparation and current server previews now require at least3 remaining event slots; frozen historical preview/admission replay and original retries remain valid. Read-only capacity-v1 complete evidence reports remaining slots and best-case terminal paths, never guarantees settlement or authorizes submission. Fifty distinct cases passed across capacity, pooled faults, preparation and pool APIs. Main14 related tables empty, grants/Live disabled, five services ready. Next: durable overflow/retention settlement design and freshness/health gates; arbitrary partial-fill terminal headroom is not solved by a3-event entry gate. No additional schema/dependency/frontend/network changes.

## Public data quality foundation — schema0023 unchanged

Read MARKET_DATA_QUALITY.md and PHASE_9_MARKET_QUALITY_REPORT.md. Market status independently rejects stale/future/malformed/mixed-generation ticker/book snapshots. New no-store public quality reports add repeatable-read stored rules and three recent contiguous closed1m bars; bounded offline replay verifies recorded observations without implying current freshness, atomic cross-store capture or authenticated origin. Ninety-eight distinct cases passed across quality and existing market/model/worker/integration coverage. Real BTC/ETH endpoint captures and CLI replay healthy; five services ready, main14 related requested/pool tables empty, grants/Live disabled. No schema/dependency/frontend/network changes. Next: opt-in persisted request-linked health policy/evidence and write-boundary checks, preserving all old immutable admissions; overflow/retention remains a separate unfinished dependency.

## Opt-in internal persisted health admission — current head0024

Read PAPER_REQUESTED_HEALTH.md and PHASE_9_REQUESTED_HEALTH_REPORT.md. Untouched controlled canonical Spot accounts can explicitly enroll immutable policies. Internal PREPARE/first SUBMIT require server-owned current quality capture; request/risk/health/pool/source/owned dispatch evidence commits atomically. Report age and underlying market freshness are rechecked at database checkpoints and before journal write return. Full audits require complete health receipts; original retries and late settlement preserve historical evidence. Eighty-eight distinct cases verified. Main sixteen related tables remain empty, grants/Live disabled, five services ready; frozen Alembic check clean at0024. No dependency/frontend/network changes. Next: independently scoped health enrollment/read and HTTP server provider routing while preserving default-disabled main; archive/overflow and broader risk/production remain incomplete.

## Scoped health HTTP admission and complete export — head0024 unchanged

Read PAPER_REQUESTED_HEALTH_API.md and PHASE_9_REQUESTED_HEALTH_API_REPORT.md. Independent ENROLL_HEALTH/READ_HEALTH actions and explicit account grants protect new enrollment/capture POSTs. Existing authorized prepare/source/owned-source/pool commands now use only the server-owned cache; denied scope precedes capture. Full bounded financial/control/health exports replay mandatory recorded receipts offline. Current-market failures roll back new work, exact retries preserve old evidence, and no historical enrollment is retrofitted. Sixty-five distinct acceptance/regression cases passed after fixing pool fixture identity and accidental preview/read keyword forwarding. Five services ready; main16 tables empty, grants/Live disabled; startup draft saved. No schema/dependency/frontend/network changes. Next: explicit overflow/archive settlement lifecycle design; broader OMS/risk/private identity/production remain unfinished.

## Repeated-information terminal space protection — head0024 unchanged

Read PAPER_REQUESTED_OVERFLOW_DESIGN.md and PHASE_9_INFORMATION_CAPACITY_REPORT.md. Repeated ACK/confirmed RECEIPT rows cannot consume minimum terminal space at current preview/write boundaries; historical receipts and frozen previews retain their replay semantics. BUY/SELL funding survives refusals; remaining full fill/SEAL succeeds after STOP. Proposed overflow inbox and segmented archival contracts are documented but not implemented. Cancellation/rejection exhaustion is explicitly reproduced without releasing holds. Next: bounded typed STAGED_UNAPPLIED envelope/identity contract, then durable inbox storage and segment application acceptance. No operator/Live activation or main fixture data.

## Unified five-block local batch — current schema0025

FIVE_BLOCK_EXECUTION_PLAN.md is the authorized whole-programme plan; FIVE_BLOCK_FOUNDATIONS.md records this batch's real boundary. Local staged-unapplied inbox and independent stage/read actions preserve financial state and immutable receipt history; current presence observations do not rewrite old acknowledgements. Selected-account common-minute exposure uses audited repeatable-read input without counting cash holds twice. Fixed-host private Spot GET signing/normalization is fixture-tested, credentials and real exchange acceptance remain absent. Causal bounded walk-forward API/export and offline selection replay are implemented; job/UI/AI remain incomplete. Main and populated-fixture snapshot/isolated restore with row-content SHA256 passed; configured CI is unverified remotely.85 distinct selected cases and typecheck passed, schema check clean. Main17 requested/pool tables empty, grants/Live disabled, five services ready. Continue whole programme without routine confirmation; next dependencies are segmented inbox application/OMS integration and enforced portfolio policies. Do not claim five complete production blocks or unattended execution after the turn.

## Bounded inbox application — schema0025 unchanged

Owned application now identifies staged immutable input by ordinal/hash, checks
current financial/control revisions and unexpired ownership, and writes through
the existing audited source/journal transaction and server health gates. Separate
APPLY_LOCAL_INPUT authorization defaults disabled. Current-authority retries
return original source receipts; staging receipts never change. Next OMS gate:
a versioned segmented journal and archive replay contract with preserved global
sequence/dedup/funds. This increment does not widen existing capacity limits.
