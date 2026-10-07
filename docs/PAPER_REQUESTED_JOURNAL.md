# Durable explicit-quantity Paper journal

`paper-requested-journal-v1` in `core/paper/requested_journal.py` persists the independent requested-quantity contract using PostgreSQL schema0015. It uses three new tables and a separate account namespace. It never imports balances, fills or ownership from existing closed-bar accounts, never dual-writes SQLite and never submits external orders. No API, UI or scheduler exposes this engine yet.

## Account and transaction boundaries

`create` records an explicit local Paper opening balance, instrument identifier and clock. These are simulation inputs, not deposits or authenticated exchange balances. The opening evidence is immutable. Account creation retries use PostgreSQL insert-on-conflict and then lock and verify the existing opening; conflicting inputs fail.

`prepare` takes a fully frozen `paper-requested-execution-v1` request. It locks the account, verifies all prior evidence and the current balance, checks the exact base and chronological request clock, then atomically saves the immutable request and its active-account cache. The immutable request commits the full cash/inventory reservation before submission or any fill. Only one unsealed request is allowed per account; a different concurrent request cannot reserve the same account resources. A stable client request identity accepts exact retries and rejects changed inputs. Caller-provided rules and clocks are validated local simulation inputs, not exchange capabilities or identity permissions.

`accept` appends one next source event, its exact reducer summary and both hashes in the same transaction as the account balance, revision and active request. Fill fees, cash, inventory, basis and realized PnL follow the existing explicit contract. Cancellation acknowledgement keeps the remaining hold. Only local source sealing releases that hold and permits another request. There is no separate balance credit on cancellation. New facts after sealing fail. Source gaps fail without buffering; callers must deliver a complete source prefix.

Non-PostgreSQL engines and autocommit connections are rejected before mutation; an ORM transaction wrapper alone is insufficient when the driver commits each statement. Account locks serialize creation, preparation, acceptance and inspection. Immutable request/event rows have database UPDATE/DELETE guards; opening fields and account revision transitions are guarded. Read verification reconstructs request bases, every event prefix, settlement summaries, sequence/identity/hash/clock evidence and the final account cache. Missing, conflicting or corrupt evidence fails closed; inspection performs no repair. Derived holds are durable through the immutable request and event journal, rather than a separately mutable reservation counter.

Exact retries return the current summary of the original request and do not increment revision, including old-request retries after a subsequent request starts. They do not return or alter the newer request's summary. Request identity belongs to its frozen account. There is no leased dispatch owner or remote query-before-retry integration in this module; PostgreSQL row locks are the local transaction boundary.

## Capacity and scope

At most 100 requests and 1,000 unique retained events per account are supported; the complete inspection is bounded to 32 MiB. Individual contract transcripts retain their delivered-event and financial bounds. Capacity failure rolls back; exact retained retries still work. There is no pruning, pagination or continuous scheduling. Inspection checks every prefix, so no production throughput or long-load claim is made.

`read(engine, account_id)` returns verified opening, current account, active request, revision and bounded complete request transcripts/summaries. This library read is transactionally coherent. Existing offline request verifier can validate separately captured transcripts; no standalone full-journal export verifier is claimed.

Tests create only isolated fixture databases. No demonstration account or fabricated trade is inserted into the main database. Current startup applies schema0015 and restarts the existing five services, which keep using their existing engines. Dependencies, hosts and credentials are unchanged. Live remains disabled.

Next work: bounded read-only API/inspection and offline journal verification, then explicit control/risk gates and ownership acceptance before exposing commands or scheduling this new engine. Shared capital, human identity authorization, external reconciliation, authenticated finality and Live remain incomplete.
