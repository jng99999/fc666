# Scoped local dispatch query

POST `/api/v1/paper-requested/dispatch-queries` requires the independently granted QUERY_DISPATCH action, exact account scope and the existing server bearer token. Defaults remain disabled. Strict input contains account_id, request_id, original client_id, owner and ownership_token. No new schema or dependency is required (head0020).

Under the account lock, complete financial, control, source, ownership and dispatch evidence is audited. The request must belong to the account and have declared original dispatch evidence. The current owner/token must be unexpired both before and after query evaluation. Takeover keeps the original client ID; stale owners and different IDs fail closed. Expired closed requests remain inspectable through existing dispatch exports, but cannot use this leased endpoint without an unexpired claim.

The response contains complete account evidence and a selected historical request assessment. UNKNOWN outcomes retain holds. Every result prohibits resubmission and external querying; no venue observation, remote absence, retry authorization or lease renewal is implied. This is a local operator capability, not multiuser identity. Existing GET exports retain their local read-only access behavior.

`python -m scripts.verify_requested_dispatch_query export.json` verifies complete evidence offline, rejects duplicate JSON keys and bounds input to32MiB. Observation-time validity does not establish future lease validity or authenticate the exporter.
