# Scoped pool preparation and whole-pool disclosure

Three independent default-disabled actions protect POST endpoints:

| Action | Endpoint | Strict body |
| --- | --- | --- |
| PREVIEW_POOL_PREPARE | /api/v1/paper-requested/pool-preparation-previews | pool_id + existing local preparation proposal |
| POOL_PREPARE | /api/v1/paper-requested/pool-preparation-commands | pool_id + proposal + local_preview_sha256 |
| READ_POOL | /api/v1/paper-requested/pool-captures | pool_id |

The existing server bearer token, exact action, exact `paper_operator_pool_ids` grant and target account grant are checked before storage. Pool actions cannot be configured without explicit pool IDs. Inside the pool transaction, immutable definition/membership is checked and every member account must appear in paper_operator_accounts before financial account locking/audit. A target-account grant alone cannot disclose other members. Scope denials return generic403/no-store with no member names or partial financial evidence. Neither pool action implies another action, ordinary PREPARE, pool creation, ownership or source delivery. No HTTP pool creation is exposed.

Preview combines the coherent whole-pool capture with the target's independently replayed local risk/funding preview. It may return capital_forecast_allowed=false when the pool cap is exceeded; this is read-only and never authorizes submission. The command uses **the nested local_preview.sha256**, named local_preview_sha256, not the wrapper's hash. The wrapper hash verifies the forecast snapshot offline; command acceptance rechecks the current pool state under locks rather than assuming the earlier forecast remains valid. Account revisions, local risk and full funding are still required. Exact committed retries return original mandatory receipts without another hold, after current whole-pool scope/audit checks.

Successful command receipts and read captures contain all member financial evidence. Access requires whole-member account scope even for the target's old receipt. Preview verification is available through `python -m scripts.verify_shared_capital_preview preview.json`, with duplicate-key and32MiB input bounds. Preview receipt/capture versions are distinct; schema0023 unchanged.

Missing pool/account404, conflicts/unverifiable409, storage/lock failure503 and scope403 use generic no-store responses. Existing local unauthenticated single-account GET inspection routes retain their earlier local capability; these additions are not a multiuser ACL or identity system. Offline hashes cannot authenticate origin or physical transaction commit. Internal libraries may omit authorized_accounts for trusted callers; HTTP always passes explicit server grants.

No token, grants or fixture pools are installed in main. No dependencies/frontend/network/schema changes. SELL inventory, transfers, freshness/health/aggregate marked risk, scheduling and private venue transport remain incomplete. Live disabled.

Next: pool command process-loss/lease/control fault acceptance and stronger retention/history limits before scheduling or broader risk integration.
