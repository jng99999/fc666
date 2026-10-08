# Stored assessment lineage and bounded processing

Record, exact retry, historical capture and export now bind every stored receipt to a verified current dispatch snapshot taken under the same account lock. A future/regressed observation or alternative financial/control/source/claim lineage fails closed before accepting stored evidence. New records reuse that current snapshot; final lease expiry checks still occur after validation/insert. Historical reads do not require a live lease.

Before materializing optional assessment payloads, PostgreSQL sums their actual serialized JSON bytes for this account/request. The32MiB stored-request budget is distinct from the existing32MiB canonical receipt/export bounds; serialized JSON can include extra formatting/escaping. Every access rejects an exceeded stored budget with no partial result. A new insert is checked again inside the transaction and rolls back if it exceeds the budget. Sixteen-record limits remain. Existing over-budget historical records would be inaccessible through these bounded paths; no records are deleted or truncated. Current main has no such records.

This budget bounds optional assessment payload loading; it does not establish a whole-process memory bound, since current account audit/export/replay allocations also exist. SQL aggregation checks the bounded request history before ORM payload loading. Financial/control state and external send capability are unchanged.

Schema0021, versions and command grants remain unchanged. Next major dependency: a shared-capital reservation contract and cross-account admission design before scheduling or any private transport. Live remains disabled. PROJECT_PROGRESS.md tracks whole-platform limitations without a misleading percentage.
