# Durable local assessment attempts (head0021)

`requested_attempts.record(engine, command)` is an internal PostgreSQL-only library capability. There is no HTTP write route or new operator grant. Each record is a local failure-assessment attempt: **remote_send_performed=false**. No venue transmission, authenticated incident, remote absence or actual transport attempt is asserted.

An exact command contains account_id, request_id, original client_id, owner, ownership_token, attempt_id, failure, expected_financial_revision and expected_control_revision. The account lock serializes records with financial/control/ownership operations. New records require current checkpoints, valid original dispatch proof and an unexpired current lease before/after persistence. Complete query and boundary evidence is captured by the server, not supplied by the caller. Financial/control revisions and holds stay unchanged.

Attempt IDs are unique per request. Exact retries return the immutable original record without renewal or another assessment; conflicting retries fail. Retry still requires the original owner/token to be current and unexpired. A takeover uses a new attempt ID/token while retaining original client identity. Each request permits16 records, each bounded32MiB; no unbounded polling loop is supported.

Schema0021 adds requested_paper_attempts with dispatch/account/claim foreign keys and UPDATE/DELETE rejection. Populated downgrade is refused; empty downgrade/upgrade is supported. Legacy records and existing export versions remain unchanged. Optional assessment evidence is verified through this library and is not required by existing financial journal APIs. This feature does not turn optional records into mandatory transport provenance.

`capture(engine,account_id,request_id)` audits the account and returns the ordered recorded assessments with complete historical evidence; lease expiry does not prevent historical inspection. `python -m scripts.verify_requested_attempt attempt.json` verifies a single receipt offline with duplicate-key and32MiB bounds. Offline evidence hashes do not authenticate origin or grant future transport authority.

Next: separately scoped local assessment commands/read exports and stronger process-loss/locking acceptance before any scheduler or transport adapter. Live remains disabled.
