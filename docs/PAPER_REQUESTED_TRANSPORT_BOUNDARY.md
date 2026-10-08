# Offline transport failure contract

`core.paper.requested_transport_boundary.evaluate(query_report, failure)` classifies TIMEOUT, DISCONNECTED, EMPTY_RESULT, CONTRADICTORY_RESULT and UNSUPPORTED_TRANSPORT against a fully verified original-client query export. This is an offline contract, with no transport implementation, API grant, scheduler, database writes or remote observations.

Every classification preserves the exact selected request funding and original client identity. It cannot release funds, ingest facts, prove remote absence or authorize submission/retry. Open requests require verified local input through the existing separately granted event path; sealed local histories request evidence inspection. Local sealing remains no proof of any venue result. Failure strings are proposed classifications, not authenticated incidents. Unsupported classifications, changed evidence and resealed decisions fail closed.

`python -m scripts.verify_requested_transport_boundary export.json` independently replays the complete nested financial/control/source/ownership/dispatch/query evidence, rejecting duplicate JSON keys and input beyond32MiB. A hash detects changes within a report but does not authenticate its origin. Observation-time leases do not authorize later transport.

No schema/dependencies/startup changes; head0020, disabled operator defaults and Live restrictions remain. Next: durable local transport-attempt evidence and transaction failure acceptance before exposing any dispatch adapter. Actual venue transport remains Not Implemented.
