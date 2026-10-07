# Atomic local source receipt acceptance

Schema0018 implements independently granted, default-disabled INGEST_EVENT commands and complete source exports/offline reconstruction. New local event declarations bind immutable receipts to original preview/financial/control checkpoints and event/settlement hashes, in the same PostgreSQL transaction as the event, applicable SUBMIT gate and account update. Exact retries return the initial receipt after later fills, controls and new reservations. Old internal events remain explicitly unlabeled and cannot be retroactively tagged. Existing financial v1/v2 JSON shape and revision semantics are preserved.

Acceptance covers separate action/account authentication before storage, strict source/clock/version/digest conflicts without effects, concurrent same/conflicting delivery, unknown full holds, stopped late fills, v2 void prefixes, full fill/SEAL and later request preservation, exception after receipt insert rollback, genuine SIGKILL before commit and after lost acknowledgement recovery, immutable receipts/event declarations, missing/rehashed corrupt persisted evidence blocking financial/control/source reads, resealed export tampering and database-independent CLI verification. Populated source evidence prevents migration downgrade; the empty migration roundtrip remains supported.

Selected regression command:

```bash
.venv/bin/python -m pytest -q --tb=short \
  -W error::pydantic.warnings.UnsupportedFieldAttributeWarning \
  tests/test_requested_sources.py tests/test_requested_journal.py \
  tests/test_requested_controls.py tests/test_requested_inspection.py \
  tests/test_requested_preparation_api.py tests/test_requested_event_preview_api.py \
  tests/test_models.py tests/test_integration.py::test_migration_roundtrip_and_hypertable
```

The downgrade-protection case was added after regression collection and run separately. Validation:149 selected regression cases passed in484.60s; the separate downgrade-protection case passed in11.57s, for150 distinct accepted cases. All19 source cases are covered across the two runs. Existing Starlette/httpx TestClient deprecation is unrelated; no warning suppression or dependency changes.

Main upgraded to0018 using bash scripts/start.sh; only owned processes stopped and all API/web/research/market/paper readiness checks restored. Frozen Alembic check reports no new operations. Main eight requested-engine tables remain empty; no token/grant or fixture capital/trade installed. Actual HTTP readiness200, default-disabled source command503 and missing source export404/no-store verified. No frontend change/build, private transport, new host/credential/service or unrelated broad test rerun. Existing .env/volumes, checkout/branch, proxy/CA retained. Startup instructions saved as a draft only; publication and independent fresh-task restoration are separate.

Receipts identify caller-declared LOCAL_PAPER_OPERATOR_INPUT, not signed venue provenance, human identity, authenticated completeness or proof of external submission. Complete export bounds remain100 requests/1000 events/32MiB;2-second lock wait is not an end-to-end deadline or load-acceptance claim. SQLite submission laboratories remain isolated. Next: controlled submission ownership and unknown-result recovery against this requested-account protocol before transport/scheduling. Live remains disabled.
