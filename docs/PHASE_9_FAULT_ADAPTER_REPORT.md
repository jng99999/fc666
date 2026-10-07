# Phase 9 — durable isolated Paper fault adapter

Implemented FaultAdapter in core/paper/fault_adapter.py using a dedicated SQLite laboratory file. The previously pure paper-fault-reconciliation-v1 contract now has independent durable request creation, immutable event delivery and linked reconciliation evidence. Production PostgreSQL schema remains0013; local Paper engine, original ledgers and exports remain unchanged. No new dependency, service, credential, network destination, API or browser UI is required.

Requests precede event acceptance. BEGIN IMMEDIATE serializes writers; events and exact cumulative evidence commit together. Identical request/event retry reuses facts; conflicts, missing source sequence, overquantity and corrupt/missing evidence fail closed. Reordered batches require a complete source prefix. Cancellation does not discard late fills. A later fill invalidates an older receipt until a matching cumulative receipt arrives. All summaries keep execution and external reconciliation disabled.

Full backend regression:427 tests passed (one existing Starlette/httpx deprecation warning); Alembic check reports no new operations; API/web/research/market/paper all functionally ready with trading disabled.

Acceptance:38 focused tests passed (18 adapter,20 pure contract). Real independent Python processes are SIGKILLed before evidence/transaction commit and after commit/before response. Reopening the file verifies rollback in the first case and exactly one retained local effect under redelivery in the second. Concurrent delivery, full1000-event retries, immutable triggers, altered request/event/evidence digests, deleted intermediate evidence and unrelated database refusal are covered. This is local process-interruption acceptance, not infrastructure/power-loss or exchange exactly-once proof.

No product accounts or synthetic market records are created by these laboratory tests. The adapter stores its own isolated transcript; it does not debit cash, update inventory, submit exchange orders or grant execution permission. Prior frontend build/browser results are unchanged; no frontend rebuild is required for this isolated Python-only change.

Next: durable simulated submission ownership/fencing and unknown-outcome query-before-retry semantics. Product economic integration and full OMS remain separate incomplete goals; Live remains disabled. Continue under the existing autonomous mandate without routine reconfirmation.
