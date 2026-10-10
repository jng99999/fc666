# Remaining delivery programme

Execute against the existing checkout; routine implementation, installation, validation and commit/push are already authorized. Preserve main financial history and keep Live disabled until its actual identity/reconciliation/execution gates pass. Passing a local test or health check does not establish production readiness. Use isolated databases for fixtures. Record concrete evidence and remaining failures for each milestone, not estimated completion percentages.

| Order | Goal | Acceptance requirement | Status |
| --- | --- | --- | --- |
| 1 | Whole-pool operational archive | Coherent pool/all-member locks; full financial/control/source/lease/dispatch/health/attempt/inbox evidence plus policy and historical admissions; offline replay rejects missing members, mixed checkpoints and forged authority | Implemented; bounded local integration passed |
| 2 | Complete recovery acceptance | Restore a populated isolated backup; compare all immutable evidence and archive replay before/after restart; fail safely on missing/corrupt artifact; main database unchanged | Populated pool/hold/source/claim/dispatch/attempt/inbox restore replay passed; process-kill, infrastructure and independent-storage gates remain |
| 3 | Segmented journal application | New version preserving frozen v1/v2 contracts; global sequence/fill deduplication, funding continuity, account/request/byte exhaustion and authoritative archive reachability | Pending |
| 4 | Scheduling and fault recovery | Current fences and grants, STOP/late fills, crash before commit, lost reply, takeover, pool-wide atomicity and durable retry/backpressure acceptance | Pending |
| 5 | Portfolio/risk completion | Explicit shared inventory/transfer semantics, price/valuation provenance, drawdown limits and scheduler enforcement, with numerical and concurrency acceptance | Pending |
| 6 | Private connector and reconciliation | Authenticated account/order/fill snapshots, complete pagination/cursor coverage, identity isolation, durable discrepancy resolution and restart reconciliation | Code work can proceed; real credential acceptance externally blocked |
| 7 | Research completion | Reproducible strategy SDK/sandbox, matching/data coverage, bounded optimization and research exports; AI outputs have measured provenance and no implicit trading authority | Pending |
| 8 | Production delivery | Configured target, CI release, independent backup storage and retention, monitoring/alerts, sustained load, infrastructure failures and rollback/restore acceptance | Local tooling can proceed; target acceptance externally blocked |

Private exchange credentials and the actual production target must be configured securely, never copied into tracked files or chat. Recheck bindings before concluding they are missing. These external dependencies do not block local code, isolated integration or recovery work. Do not claim external deployment, real reconciliation or a clean full-suite run without its completed evidence.

Archive milestones alone do not enable overflow application or historical retention. Hashes establish consistency rather than trusted provenance; full operational restore includes database constraints and immutable receipts beyond exported projections. Each completed milestone should update this table and PROJECT_PROGRESS with executed checks and scope limitations.
