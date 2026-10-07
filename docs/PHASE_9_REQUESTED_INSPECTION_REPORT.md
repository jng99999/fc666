# Complete requested-quantity journal inspection acceptance

Implemented a GET-only complete-account export API, `paper-requested-journal-export-v1` deterministic offline verifier and bounded CLI. PostgreSQL inspection checks immutable opening/request/event evidence and every stored prefix settlement before export. Offline replay checks exact chained bases, identities, clocks, complete ordered source events, request settlements and final account/revision. Schema remains0015.

## Executed checks

- 125 tests passed in274.47 seconds across initial29 offline inspection tests,12 API/timeout tests,21 durable journal tests (including real subprocess SIGKILL),40 explicit execution tests,22 old PostgreSQL projected-funding cases and the original API readiness/no Live route check.
- Final offline inspection suite:32 passed in1.73 seconds after three additional wrong-base, unsealed late-fill reservation and export-wrapper/CLI file-bound cases. Thus128 distinct selected tests passed. One existing Starlette/httpx deprecation warning remains.
- Independent fixture arithmetic covers full BUY followed by partial SELL and late cancellation fills: final cash771.725, inventory2.5, basis252.5, realized PnL24.225 and fees5.775; revision11 and nine source events. Empty and pending histories retain their actual scope and reservations.
- Rehashed wrong balances/summaries, deleted/reordered/duplicate events, duplicate stable client identities, wrong account/base/clock, unsupported envelopes and JSON boolean/integer substitutions fail. CLI rejects duplicate JSON keys, oversize files and semantic tampering. It succeeds with an unusable database URL, demonstrating offline operation without database access.
- Actual PostgreSQL/API fixtures verify GET idempotency and unchanged records, opaque Unicode/slash query IDs,404/409/422/503/405 separation, generic failures without partial reports, no-store headers, complete export verification and previous Paper list/health/Live-route behavior.
- During an uncommitted fill INSERT the API waits on the account lock, then returns one fully committed revision and exact balance. A held account lock times out with503 and subsequently recovers without changes. Missing new journal table makes readiness503.
- Frozen Alembic check found no new upgrade operations. Main database head remains0015; all three requested-quantity tables remain empty. No fixture balance or fabricated product trade was inserted.
- Restarted only the owned API; web/research/market/paper stayed running and ready. Live remains disabled. Running API OpenAPI declares only GET for the new route; unknown-account smoke returned expected404/no-store. All five functional readiness checks passed.
- No frontend change/build/browser test, new migration, dependency, credential, network destination or startup configuration was needed. Existing saved install/start instructions remain sufficient; no draft update or publication is claimed this slice. No fresh full-repository test run is claimed.

## Scope and next goal

This is read-only journal inspection and internal-consistency verification. It adds no account creation/mutation command, frontend, automatic scheduling, human identity permissions, shared-capital risk or exchange transport. Checksums cannot authenticate origin, prove freshness or detect a completely rewritten internally consistent supplied history. Local source sealing remains a simulation assertion, not venue finality.

Next: versioned explicit account controls and execution-risk gates with immutable acceptance evidence before opening mutation endpoints or scheduling. Preserve old account histories, engine versions, local configuration and Live-disabled behavior.
