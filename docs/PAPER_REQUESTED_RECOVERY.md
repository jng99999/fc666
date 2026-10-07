# Local submission attribution and read-only recovery

`paper-requested-recovery-v1` derives a complete per-account recovery assessment from the verified source export. GET `/api/v1/paper-requested/recovery?account_id=ACCOUNT` captures one locked financial/control/source snapshot and makes no writes. Existing local read-route access semantics apply; this is not multiuser authorization. Offline verification: `.venv/bin/python -m scripts.verify_requested_recovery FILE`.

Every request reports original submission attribution when its sequence-zero SUBMIT has an immutable local source receipt. The stable submission_id binds the frozen request identity and original SUBMIT event hash. Attribution includes original caller-declared source, receipt hash and financial/control checkpoints; it stays stable after later control commands, fills, sealing and new requests. This ID is not a venue client-order ID or proof that anything was dispatched. Unlabeled old SUBMIT events retain null attribution. Unlabeled subsequent events are counted explicitly.

| Action | Evidence |
| --- | --- |
| NOT_SUBMITTED_LOCAL | No local SUBMIT event; not proof of remote absence |
| WAIT_LOCAL_RESULT | Local SUBMIT without an acknowledged result, including UNKNOWN_SUBMISSION |
| RECONCILE_LOCAL_PREFIX | Known local receipts/fills/cancellation/rejection, source not sealed |
| LOCAL_SEALED | Declared submission and explicit local SEAL; no claim of venue finality |
| LOCAL_VOIDED | Immutable zero-event local finalization |
| UNVERIFIED_SUBMISSION_PROVENANCE | Existing SUBMIT without declared local source receipt |

Every action sets submission_allowed/resubmission_allowed/external_query_supported/external_submission_allowed=false. UNKNOWN_SUBMISSION retains its original reservation. Cancellation acknowledgement alone retains unfilled holds. A full fill without SEAL still requires local-prefix reconciliation. STOP is permanent but does not block existing late-fill accounting. The report uses current account/revisions/active request separately from historical request funding; it never restores an old account from a previous submission.

Ownership explicitly reports coverage=NOT_IMPLEMENTED with null owner/fencing_token. Local source labels are not owner leases or authenticated actors. This slice completes the recovery/attribution contract; durable PostgreSQL claim/takeover/fencing and dispatch integration remain the next dependency. The SQLite lease laboratory stays separate and no dual-write is introduced.

The complete nested financial/control/source history is independently reverified before assessment. Missing declared receipts, corrupt/resealed inconsistent evidence and altered assessment fields fail without partial output. Hashes prove internal consistency, not signed origin, freshness or authenticated completeness against coherently replaced history. Histories retain100-request/1000-event and32MiB bounds; the2-second database lock timeout bounds waiting only. No throughput or full-request deadline claim.

Schema remains0018. No dependencies, secrets, hosts, production grants, test data or UI changes. Main commands stay default-disabled and Live stays off. Use bash scripts/start.sh for ordinary restoration; API-only changes can restart only the owned API through scripts/dev_services.py. Never seed fixture accounts into main. Processes must restart in new tasks; saved configuration is separate from publication.
