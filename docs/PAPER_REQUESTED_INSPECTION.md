# Complete local Paper journal inspection

`paper-requested-journal-export-v1` exports the complete retained history of one independent requested-quantity Paper account. It is separate from closed-bar stream inspections. Current schema head is0017. Accounts with local void facts use financial export v2; other accounts and existing captures retain v1. See PAPER_REQUESTED_FINALIZATION.md. The separate default-disabled local control-command endpoint is described in PAPER_REQUESTED_COMMANDS.md. No order execution command, account creation endpoint, scheduler or frontend has been added.

## Read-only API

`GET /api/v1/paper-requested/journal?account_id=...` returns `{version, scope, journal, sha256}`. The query identifier accepts the existing opaque account IDs, including URL-encoded Unicode, spaces and slashes, with length1–128. The journal contains opening evidence, every request and source event in stored order, exact request summaries, current balances, active request, revision and last source clock. The scope is `COMPLETE_RETAINED_SINGLE_ACCOUNT_LOCAL_PAPER_JOURNAL`; it is not a latest-row window or market valuation.

Inspection verifies the PostgreSQL immutable opening/request/event evidence and every persisted prefix summary before exporting. The account row lock prevents a GET from combining a pre-fill balance with post-fill events. It writes no journal, balance, control or revision. Account-lock waiting is limited to two seconds and resets with the transaction; this is not an end-to-end HTTP deadline or production throughput claim. Inspection still checks all retained prefixes.

Missing accounts return404; invalid query length returns422. Conflicting, corrupt, unsupported or oversized evidence returns409 with a generic detail and no partial journal. Database failure or lock timeout returns503. Successful reports and explicit404/409/503 responses use `Cache-Control: no-store`. POST on this route returns405. The route inherits the existing local API access scope; it adds no human identity authorization. Engineering readiness now checks the three requested-quantity tables as well as the schema head.

## Offline verification

Save a successful response to a file, then run:

```sh
.venv/bin/python -m scripts.verify_requested_journal /path/to/journal.json
```

The CLI opens no database or network connection. It bounds file reads, rejects duplicate JSON object keys and validates the exact export envelope/hash. Replay starts from the frozen local opening balance and reconstructs every request's full funding, unique fills, fees, basis, realized PnL, cancellation holds and source sealing. Account identity, unique client/request identities, exact chained bases and nonregressing cross-request clocks must agree. Another request cannot follow an unsealed request. Each stored source event must appear once and in contiguous order; delivery duplicates valid for the standalone reducer are not extra stored journal rows.

Final balances, active request, every request summary, source-event count, revision and last clock must exactly match reconstruction, including JSON types. Recomputing a hash does not bypass economic or chain checks. Pending and empty accounts remain valid; cancellation acknowledgement retains holds until local source sealing. Original exports are not mutated during verification.

The complete account limits remain100 requests and1,000 source events. The entire export, including wrapper/hash, must fit32MiB. There is no pagination, pruning, automatic repair or scheduling. Input market rules and opening balances remain local simulation inputs.

The checksum establishes internal consistency, not origin authenticity. Offline verification cannot prove freshness or detect a completely rewritten, internally consistent history whose balances, identities, revision and checksum were all replaced. Completeness describes the server's verified retained snapshot and supplied replay scope; it is not a signed proof of external account history or exchange finality.

## Development scope

Fixtures use only isolated databases and temporary exports. Main requested-quantity tables stay empty; no demonstration balance or fabricated product trade is created. Restart only the owned API for this Python API change, then run scripts/dev_services.py start/status to verify all five services. Existing install/start instructions remain sufficient; no new migration, dependency, credential, host or startup configuration is needed. No frontend build or browser claim applies.

Explicit opt-in account controls and request-risk gates are now available; see PAPER_REQUESTED_CONTROLS.md. Local unsubmitted finalization is implemented; see PAPER_REQUESTED_FINALIZATION.md. Next: explicit permission and fencing contracts before exposing mutation commands or scheduling. Shared capital, leased external dispatch ownership, private reconciliation and Live remain incomplete. Live stays disabled.

Current access boundary: PAPER_REQUESTED_COMMANDS.md describes the opt-in local control-command capability and its limitations. Local finalization and economic/source operations still have no HTTP write routes.
