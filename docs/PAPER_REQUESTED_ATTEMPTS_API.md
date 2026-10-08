# Independently scoped local assessment commands and exports

POST `/api/v1/paper-requested/assessment-commands` requires RECORD_ASSESSMENT; POST `/api/v1/paper-requested/assessment-exports` requires READ_ASSESSMENTS. Both require the existing server bearer token and exact account scope; defaults are disabled. Neither grant implies the other, QUERY_DISPATCH, ownership claim or event delivery. These are local operator capabilities, not multiuser actor identity.

The strict write body is the internal attempt command documented in PAPER_REQUESTED_ATTEMPTS.md. New writes fence current financial/control revisions and original client/current lease. Exact retries return the original receipt while the same lease remains current/unexpired. No duplicate receipt, lease renewal, event ingestion, financial mutation or actual transport send occurs. Failure classifications remain caller declarations, not authenticated incidents.

The read body contains only account_id/request_id. The account transaction audits current dispatch evidence and all stored ordered receipts for that request. Historical read requires no active lease, so expired attempts can still be inspected. Export includes the current complete account dispatch report and original historical assessment receipts, keeping current and historical states separate. Maximum16 receipts and32MiB total export; over-capacity exports fail closed without partial output. A single large persisted receipt may therefore remain verifiable individually even when aggregate export exceeds its bound.

`python -m scripts.verify_requested_attempts_export export.json` replays all supplied evidence, original dispatch identities, receipt order and duplicate IDs offline. As with other unsigned exports, an offline verifier does not authenticate origin or prove that an exporter supplied every stored record. PostgreSQL immutable records and the server's ordered query enforce runtime coverage. There is no mandatory financial-event attempt marker; optional assessment receipts remain separate from transport provenance.

Denied requests fail before storage. Missing account404, conflicting/unverifiable409 and storage/lock unavailability503 provide generic errors and no-store. Neither endpoint exposes fixture tokens or enables main defaults. Schema0021 unchanged; no dependency/frontend/network changes.

Next: reference-backed historical prefix acceptance and stronger export completeness contracts before local adapter scheduling. Venue transport, shared capital, multiuser identity and Live remain incomplete.
