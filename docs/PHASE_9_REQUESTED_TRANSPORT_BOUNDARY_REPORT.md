# Acceptance: offline transport failure boundary

Implemented complete nested-evidence failure assessment and independent bounded offline CLI. No transport API, credentials, dependency, schema, service configuration or financial mutation was introduced.

Four test cases passed: new failure-matrix acceptance plus three scoped-query regressions (18.65s). The matrix covers five failure classes across submitted, unknown, acknowledged and partially filled states (20 assessments), original client identity, retained404/303 cash holds, resealed funding/retry/absence tampering, unsupported inputs, offline CLI and duplicate JSON rejection. Source exports remain identical across every evaluation. Existing TestClient deprecation warning remains.

All five services report ready. Head0020 and existing saved startup instructions remain sufficient; no draft modification needed. The contract is offline only: it does not establish that an incident occurred, that a venue received an order, or that remote absence is proven. Live remains disabled.

Next: durable local transport-attempt evidence with rollback, expiry and recovery acceptance before enabling any adapter.
