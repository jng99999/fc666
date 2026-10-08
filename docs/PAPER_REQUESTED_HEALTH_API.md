# Scoped local Paper health HTTP interface

This extends PAPER_REQUESTED_HEALTH.md at schema0024. It implements explicit local operator access; it is not a multi-user or production identity system. Main operator grants and Live stay disabled. No schema, dependency, frontend or domain changes are needed.

All new endpoints are authenticated POST requests under `/api/v1/paper-requested`, with `Cache-Control: no-store`. Actions are independently configured in `paper_operator_actions` and require explicit account membership in `paper_operator_accounts`. ENROLL, PREPARE and INGEST_EVENT do not imply either health permission. Disabled configuration returns503, missing/bad bearer401, denied account/action403 before storage/cache. Missing accounts return generic404, conflicting/unverifiable evidence409 and storage/lock failure503.

| Endpoint | Action | Strict request |
| --- | --- | --- |
| `health-enrollment-commands` | ENROLL_HEALTH | account_id, expected_control_revision integer>=1, expected_financial_revision integer exactly0 |
| `health-captures` | READ_HEALTH | account_id |

Unknown fields, booleans/numeric strings for revisions and client health/cache JSON are rejected. Enrollment derives an immutable policy from the actual opening and control checkpoint. First enrollment requires an untouched controlled supported canonical Binance public Spot account. Financial revision0 explicitly names that original enrollment checkpoint. Exact retries use the original enrolled control revision and return the original policy even after later financial/control activity; they do not reinterpret revision0 as a current checkpoint. Conflicting retries and retrofits are denied. Enrollment responses disclose only the enrolled policy, not the current financial history. Enrollment does not acquire market data or imply current health.

Authorized existing preparation, source, owned-source and pool preparation commands now pass the application's server-owned Redis client into the existing health guard. No request can select the cache/provider or supply an approval. Unenrolled commands retain prior behavior without acquiring health cache data. Enrolled new PREPARE and first SUBMIT capture quality and recheck its report age and underlying freshness at database checkpoints; all evidence and state commit together. Unhealthy data denies new work with rollback. Exact historical retries and late settlement do not need current data. Whole-pool member scope remains required before cache access. Financial previews do not constitute health approval.

`health-captures` locks the account and performs the full journal audit, then exports the declared health version, actual policy, sorted complete gates, financial inspection and control history. It does not acquire Redis or modify state. SQL stored evidence budgets are checked before loading; the complete export is capped at32MiB. Undeclared accounts have null declaration/policy and an empty gate list. Reads never invent health evidence for old admissions.

```
.venv/bin/python -m scripts.verify_requested_health /path/to/export.json
```

Offline replay validates the financial journal, controls, policy/opening linkage, each historical health predicate, exact mandatory PREPARE/SUBMIT coverage, phase/request/control bindings, deterministic order and envelope hash. The CLI bounds input before parsing and rejects duplicate object keys. Missing, duplicate, orphaned, reordered or wrongly bound gates cannot be repaired by merely recomputing the outer hash.

The export proves consistency of the supplied recorded history, not authenticated venue origin, provenance of a rewritten entire history or a current authorization. Rule freshness and atomic cross-store capture remain unverified. There is no external transport or Live activation. Settlement overflow/retention, scheduler integration, broader exposure and production acceptance remain separate unfinished work.
