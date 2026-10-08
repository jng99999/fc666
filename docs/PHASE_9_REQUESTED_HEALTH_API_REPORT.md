# Phase9 scoped health interface acceptance

Scoped HTTP health enrollment/read and server-owned health provider routing now extend the persisted health engine at0024. See PAPER_REQUESTED_HEALTH_API.md for the access, retry and export contracts. No migration, dependency, frontend build or network configuration change was required.

New isolated PostgreSQL HTTP acceptance covers independent token/action/account grants before storage, strict bodies, default disablement, original enrollment retries after later activity, refusal to retrofit requests, read-only unenrolled/enrolled exports, generic missing/storage/lock errors and no-store responses. Real new preparation and source/owned-source SUBMIT use fixture data through the actual application cache client. Unhealthy data leaves financial revisions and gates unchanged; healthy data commits; retries succeed with unavailable current data. Pool member scope denies before cache capture and authorized pooled preparation persists a health gate.

Offline acceptance verifies complete recorded financial/control/health replay, missing/duplicate/reordered gates, wrong policy identity/declaration/control history despite resealed outer hashes, duplicate JSON keys, pre-parse32MiB file bounds, export-size refusal without mutation and financial-v2 export after void without live data. These exports establish recorded consistency, not authenticated origin or current execution permission.

Validation results and runtime checks are recorded below after completion. All generated balances, controls, orders, policies and cache data remain confined to isolated fixtures; main data is not seeded.

| Final verified scope | Distinct passed cases |
| --- | --- |
| Scoped health API/export | 9 |
| Persisted internal health engine | 22 |
| Existing preparation API | 16 |
| Existing shared-capital API | 7 |
| Existing ownership API | 11 |
| Total | 65 |

The first combined run had57 passes and6 failures: one new pool fixture used a mismatched noncanonical allocation identity; five existing pool API cases exposed an erroneously forwarded health_cache argument on preview/read calls. Both causes were corrected. The full pool API plus healthy pooled preparation rerun passed8 cases; the final health API file passed9. Internal health, preparation and ownership regressions passed in the combined run. No failing or skipped acceptance cases remain in these65 distinct cases. Only the existing Starlette/httpx deprecation warning was observed. No unrelated full-project or frontend suite was run for this backend-only slice.

The owned API process was restarted with final source. All five development services report ready; /health/ready returns200. Main schema remains0024 and all16 requested/pool tables remain empty. Operator token/account/action/pool grants remain absent and Live false; both actual new endpoints return503/no-store under default configuration. Git whitespace validation passed. Updated complete start_skill was saved successfully; install/network/credentials/repository membership were preserved. Draft persistence is not publication or independently tested fresh-task restoration.

Next authorized work: settlement overflow/archive design and lifecycle capacity acceptance. Broader scheduler/risk integration, private exchange transport, identity, aggregate exposure and production deployment remain unfinished. This slice grants no external submission permission.
