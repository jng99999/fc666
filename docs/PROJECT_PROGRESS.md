# FC666 progress and remaining scope

The documented goals have unequal size; counting completed slices or passing tests does not establish a whole-platform percentage. This status follows AUTONOMOUS_EXECUTION_PLAN.md and its acceptance reports, not an assertion that the platform is production-ready.

| Goal | Current scope | Remaining |
| --- | --- | --- |
| 1 Persisted observations | Phase gate completed | Broader production capacity/retention |
| 2 Comparable history | Phase gate completed | Future data/source expansion |
| 3 Sampled visual analysis | sampled-v1 gate completed | Continuous analytics not implied |
| 4 Risk observability | Limited hint/policy/history, public-data quality replay and opt-in persisted request health gates and scoped HTTP enrollment/read | Full monitoring and production execution |
| 5 Execution/recovery foundations | Paper preparation, authorization, lifecycle and funding slices implemented | Integrated production OMS and private reconciliation |
| 6 Reconciliation | Pure contract and isolated durable fault laboratory implemented | Private exchange-origin reconciliation |
| 7 OMS | PostgreSQL requested-quantity journal, local control/source/lease/dispatch/query/assessment evidence | Bounded staged-unapplied inbox added; segmented application, integrated scheduling, actual transport and authenticated outcomes remain |
| 8 Execution risk | Per-account reservations, exclusive pools, atomic pooled BUY admission, scoped interfaces, fault acceptance, capacity diagnostics, repeated-information terminal space protection and scoped server-captured health gates | Long-term retention/guaranteed settlement headroom, transfers/shared inventory and aggregate exposure |
| 9 Private connectors/identity | Local operator action/account scoping | Read-only signed query library is fixture-tested; real credential custody, identity isolation and authenticated balances/orders/fills integration remain |
| 10 Production acceptance | Five development services, local snapshot/row-content restore drill and configured CI | Offsite retention/restore, monitoring, sustained load, infrastructure faults, deployment/rollback |
| 11 Research expansion | Existing EMA/SMA research, queued holdout and new causal bounded walk-forward API/CLI | Strategy sandbox/SDK, matching/data coverage, walk-forward job/UI integration and reproducible AI expansion |

Five substantial downstream work blocks remain: integrated OMS, shared-capital risk, private connectors/identity, production acceptance and research expansion. Earlier limited risk/foundation slices also need integration. No defensible completion percentage or delivery-date estimate is available from the current acceptance evidence. A live-ready trading platform remains materially unfinished.

Current main requested-engine data stays empty with operator commands disabled; Live is disabled. The detailed current slice and evidence are in the latest acceptance report. Environment draft saving is separate from publication. Continue authorized work without routine permission; do not imply unattended execution after a chat turn ends.

Unified programme: FIVE_BLOCK_EXECUTION_PLAN.md. Latest local batch and limitations: FIVE_BLOCK_FOUNDATIONS.md (schema0025). The five full blocks are not completed by delivering these foundations.

Current increment: owned local inbox application now reuses the atomic financial
and source transaction, with a separate disabled-by-default API grant, exact
staging identity, current revision/ownership fences and strict next sequence.
This closes the bounded staged-input application path; it does not complete
segmented journals or the five full production blocks.

## 2026-10-09 — cloud recovery and integrated redeployment

Original environment is connected again; preserved checkout and local changes,
verified remote b29e197, restored PG/Redis and migrated to0026. Integrated queued
walk-forward/page, atomic bounded inbox drain, and opt-in immutable pool risk
admission. Recovery evidence is documented in FIVE_BLOCK_FOUNDATIONS.md.
This is current cloud service restoration, not an externally hosted production
release or completion of the entire five-block programme.

Private connector integration now includes protected credential-file loading,
bounded snapshot export/verification and offline observation differences. This
is fixture-validated read-only tooling. Real credentials/identity and complete
financial reconciliation remain outstanding; see PRIVATE_SNAPSHOTS.md.

## 2026-10-09 — full backend regression and fixes

Full1101-case run completed:1098 passed,3 failed. Fixed Timescale-backed isolated
restore cleanup and two overbroad assessment-budget fault injections. All3
failed cases and affected paths passed the39-case focused regression, including
6 new target-guard/active-connection cleanup cases. Main backup row hashes,
schema0026 and five-service readiness passed. No clean second full-suite run,
production load or external deployment is claimed. See INTEGRATED_ACCEPTANCE_REPORT.md.
# Financial segmented archive checkpoint

Implemented bounded financial-only archive transport and protected local capture/verify CLI. Existing complete journal exports are split into64KiB canonical chunks with contiguous offsets and predecessor hashes. Offline verification reassembles and deterministically replays the full financial journal; pending funding holds remain intact. It rejects missing/reordered/truncated segments and resealed economic corruption. Capture is read-only, output is exclusive0600 and fsynced. No dependencies, migrations, runtime settings or financial capacity changed.

Validation: `pytest -q tests/test_requested_archive.py tests/test_requested_inspection.py` completed with44 passed (one existing Starlette deprecation warning), including isolated PostgreSQL capture and subprocess CLI acceptance/rejection. Five service readiness checks passed. No full-suite rerun claimed for this checkpoint. Full operational evidence manifests, segmented financial application and authorized retention remain unfinished; private exchange identity and external production deployment remain unverified.
# Coherent operational evidence archive checkpoint

Added opt-in `scripts.paper_archive capture --operational`: financial, controls, local sources, ownership leases, dispatch and health gates are read under one account lock/transaction and archived under a distinct version. Independent contract replay and identical financial/control checkpoints are mandatory; resealed scope expansion and retention/execution authority are rejected. Attempts, inbox and pool evidence remain explicitly excluded. Existing financial-only archives retain their version and semantics; no schema, dependency, service configuration or capacity changes.

Validation:34 archive/health regression cases passed, plus7 distinct operational cases passed across focused runs. These include isolated PostgreSQL capture, a real CLI operational export, differing valid control-checkpoint rejection, owned pending-request reservations and resealed authority mutations. Five services remain ready and the cloud connection is connected. No full-suite rerun or complete operational restore is claimed.
# Operational receipt archive checkpoint

Added opt-in `capture --operational --receipts` with new operational evidence-v2 and segmented archive-v2, preserving existing archive formats. Same-transaction capture now includes ordered attempt groups for every retained request and original immutable inbox receipts, with historical ownership/control/financial binding and independently recomputed application observations. Pool evidence remains excluded; event capacity, schema, grants and external execution settings are unchanged.

Validation:25 archive regression cases passed together after correcting a new test fixture that initially assumed a dispatch existed before submission; one additional populated CLI receipt capture/scope check passed separately (26 distinct passing cases). Checks cover receipt replay before/after actual local application, unchanged staging/attempt evidence, missing request groups, duplicate staged identity, resealed claim mismatch and invented application observations. Cloud connected and five service readiness checks passed. No full backend-suite rerun, retention authorization or complete operational restore claimed.
# Remaining delivery plan, whole-pool archive and restore checkpoint

Established REMAINING_DELIVERY_PLAN with eight ordered milestones and explicit acceptance requirements for archive/recovery, segmented application, scheduler/fault handling, portfolio risk, authenticated reconciliation, research and production. External credentials/target acceptance remain distinguished from local implementation work.

Implemented `paper_archive capture-pool` with a distinct bounded whole-pool archive version. Capture locks the pool and every member in canonical order, includes operational-v2 receipts, immutable risk policy and all admissions, and binds current account journals exactly plus historical multi-account prefixes. Missing/mixed coverage, policy mismatch and future observations fail. No migration, dependency, runtime settings or financial capacity changes. Existing archive versions are preserved.

Validation:34 archive integration cases passed; after adding incremental bounds and observation checks,16 pool/restore/cleanup cases passed (42 distinct relevant passing cases across both sets). Populated isolated restore includes a pending reservation, owned submission/source, unapplied staged fill and timeout attempt. Complete whole-pool archive replay matches after connection reopen and backup restore; semantic failure propagates and cleans the restore target. Main12 protected financial/pool tables remain empty, schema0026, Alembic check reports no changes, and all five services are ready. No full-suite rerun, process-kill/infrastructure recovery, independent storage or external deployment acceptance claimed.
# Independent segmented replay contract checkpoint

Implemented `paper-requested-segmented-replay-v1` and an offline verifier, preserving public frozen request/execution/journal contracts. Segments bind immutable request/header, contiguous global source ranges, predecessor hashes and independently recomputed settlement checkpoints. Immutable batch append checks full history; exact final-batch retry returns unchanged evidence. Cross-segment fill deduplication, UNKNOWN/cancellation holds, fees, inventory and cost basis use the existing arithmetic through a private explicitly bounded reducer. The new envelope supports at most128 events/128KiB per segment,256 segments,8192 events and32MiB aggregate. Database writes, worker authorization, multi-request/account continuity, archive reachability and retention remain unsupported and explicit in the artifact.

Validation:84 segmented/frozen execution/inspection cases passed, then13 segmented cases passed including one additional real8192-event boundary case (85 distinct pure/offline passing cases). Another28 isolated journal, atomic drain/pool risk and populated archive restore regression cases passed:113 distinct relevant passing cases overall. A capacity test fixture initially changed the declared grammar without rebuilding its fixture; corrected before the passing run. Verified1003 events through terminal settlement,8192 pending events with holds retained, refusal at8193, conflicting fill identities, strict event/sequence identities, count/byte bounds, resealed checkpoint corruption and CLI duplicate-key rejection. Five service readiness checks passed; Alembic check found no new schema operations. No full-suite rerun or durable segmented journal application claimed.
# Durable segmented local evidence checkpoint

Implemented owner-only local immutable snapshot nodes and atomic HEAD publication for the independent segmented replay contract, plus init/append/verify CLI. Complete objects are fsynced before exclusive digest-name publication; HEAD replacement and directory fsync establish the local commit boundary. Full predecessor/replay validation, explicit expected-node fencing, exact final-batch lost-reply retry, bounded locking and storage/file budgets fail closed. Local evidence revisions do not affect financial revisions; database financial writes and operator/worker authorization remain explicitly unsupported.

Validation:95 store/segmented/frozen execution/inspection tests passed together. Real SIGKILL acceptance covers partial-object writes, before index publication and after index publication; one committed batch remains after retry. Checks also cover conflicting concurrent writers, missing/tampered historical objects, stale checkpoints, capacities, protected regular files/symlinks, CLI with database unavailable and persisted1003-event terminal economics. The long-history fixture initially compared equivalent decimal strings by formatting; corrected to numeric comparison. Five services remain ready. No schema/dependency/runtime configuration changes, main financial writes, database segmented application or infrastructure/offsite durability claims.
# Vercel frontend deployment preparation

Added apps/web/vercel.json with Next.js, frozen npm installation and production build settings; documented branch/root selection and independent backend/API/WebSocket requirements in VERCEL_TEST_DEPLOYMENT. Frontend typecheck and production build passed. The current tool catalog contains no callable Vercel integration, runtime Vercel credential/project variable names are absent, and CLI authentication cache is absent. At that checkpoint no public deployment was verified. The user subsequently supplied https://fc666-test.vercel.app and showed the connected Vercel plugin; this conversation still lacks callable Vercel management tools, so account disconnection must not be inferred. Full API/workers cannot be supplied by publishing this frontend alone.
# Light workstation interface and explicit data-unavailable states

Replaced the dark terminal defaults with a light native-font workstation: white rounded surfaces, muted separators, segmented period controls and coordinated light chart rendering. Applied shared visual treatment to research inputs/cards and mobile layouts. Terminal strategy/portfolio/Paper tabs now link to existing workspaces; removed obsolete claims that all strategy/risk work was unimplemented. Unavailable historical data displays an actionable chart message, and a Vercel build lacking API_BASE_URL explicitly identifies frontend-only preview status. Public backend connectivity remains unverified without the actual deployed URL and backend target; no synthetic market data was added.

Frontend typecheck and final production build passed. New browser acceptance passed actual market rendering, light layout,390px mobile overflow, research entry and an explicitly injected historical-service failure state. Existing browser_terminal acceptance passed indicators, all six intervals, symbol switching, pointer/keyboard resize/persistence, fullscreen/tabs, real market-worker loss/recovery and mobile chart/book/trades, with zero browser errors (frame p95≈16.8ms, paint p95=64ms for this run). Updated candle pixel checks to the new palette without removing rendering assertions. Cloud restart required restoring retained PostgreSQL/Redis containers and five services; readiness subsequently passed. No actual public redeployment is claimed.

# Public deployment socket configuration checkpoint

Added build-time NEXT_PUBLIC_MARKET_WS_URL for direct public read-only browser WebSocket connections to a separately hosted API at /ws/v1/market. Unset configuration preserves the existing same-origin development proxy. Reject non-WebSocket schemes, credentials, query/fragment, incorrect paths and WS on HTTPS; configured deployment builds validate before compilation. This public variable is not a secret or account authentication channel. No schema, financial writes or Live enablement.

Validation:11 endpoint configuration assertions, frontend typecheck, production build and browser_light_workstation passed, including actual live data, mobile layout, research navigation and injected failure display. All five owned services restored. The supplied fc666-test.vercel.app site cannot yet be inspected: inherited cloud proxy rejects CONNECT with403 before any site response. Added only that hostname to the saved network configuration draft, preserving existing hosts; runtime policy still excludes it. Public deployment, backend target, TLS/socket admission and remote queued-job acceptance remain unverified. See VERCEL_TEST_DEPLOYMENT for separate API_BASE_URL and public socket configuration.
