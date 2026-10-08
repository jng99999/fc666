# Acceptance: pooled process-loss and exhausted history

Six new cases cover genuine SIGKILL before commit and after committed lost reply, scaled request retention, revoked whole-pool scope, STOP with retained hold, and exhausted source-event history. The last case initially failed because preview accepted a new request despite having no space to persist SUBMIT; guards were added to preview and actual journal preparation. Historical retry remains before the new preparation guard. The regression checks both ordinary and pooled preparation refusal, unchanged account evidence and original pool receipt recovery.

Validation: `UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen pytest -q tests/test_shared_capital_faults.py tests/test_requested_preview.py tests/test_requested_preparation_api.py tests/test_shared_capital_api.py` passed66 cases in145.19s. Existing TestClient deprecation warning remains. This includes prior unpooled preparation/retry/concurrency/process-loss/prefix and scoped pool API regressions. Scaled3-request/3-event limits prove boundary behavior; they do not constitute production retention or throughput acceptance.

API restarted with the fix; all five services ready and /health/ready200. Main schema0023 and fourteen related tables empty; operator token/account/action/pool grants and Live disabled. No schema/dependency/frontend/network changes. Startup draft saved successfully; it requires separate publication in environment settings. Fresh-task restoration was not tested.

Remaining: event headroom for future partial fills/terminal settlement, archive/rollover, health/freshness gates, aggregate marked exposure, scheduling, private transport, identity and production acceptance. SIGKILL of command processes is not PostgreSQL-node or disk-loss testing.
