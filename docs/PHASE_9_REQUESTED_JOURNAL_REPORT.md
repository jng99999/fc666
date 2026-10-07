# Explicit PostgreSQL journal acceptance

Implemented `paper-requested-journal-v1`, independent accounts and immutable request/event tables at schema0015. Full requested quantity is reserved before local submission. Event evidence, balance, basis, fees, realized PnL and revision commit in one account-locked transaction. Cancellation acknowledgement retains remaining holds; local source sealing releases them. Old stream accounts and SQLite laboratories are separate.

## Executed validation

- 93 tests passed across the explicit contract, initial17 journal tests, schema migration roundtrip, API readiness/no Live route, all22 existing projected-funding tests and12 old Paper-account tests; 291.03 seconds.
- Final journal suite:21 tests passed in108.90 seconds, after adding sell-chain/corrupt-summary and PostgreSQL/autocommit guards. Thus97 distinct tests passed across the selected suites. Earlier exploratory runs are superseded by these checks. One existing Starlette/httpx deprecation warning remains.
- Actual subprocess SIGKILL after request or fill INSERT before commit leaves the original durable account unchanged. SIGKILL after committed preparation or fill loses the reply but preserves exactly one result after connection rebuild/retry. This does not test host/database infrastructure failure.
- Concurrent creation and identical preparation/fill deliveries produce one revision/economic effect. Distinct concurrent full requests cannot double reserve the account. Exact retries, conflicting inputs, source gaps, stale base/clocks, cross-account requests and capacity failures are checked.
- Database UPDATE/DELETE guards protect request/event evidence. Account-cache corruption and a wrong settlement summary with a recomputed hash fail replay. Hand-calculated BUY partial/late cancellation and SELL basis/PnL plus subsequent-request balances pass.
- Non-PostgreSQL and autocommit connections reject before economic storage mutation.
- Frozen Alembic check found no new upgrade operations. Main database head is0015; new account/request/event tables all contain zero rows. All fixture accounts are isolated test records.
- Owned services stopped and restored through scripts/start.sh; existing .env and volumes preserved. API, web, research, market and paper functionally ready, trading disabled. No dependency, host or secret changes. No frontend changes/build or fresh full-repository test run claimed.
- Existing saved start_skill preserved and appended with tested0015 instructions; draft persistence confirmed. This is not publication or evidence of fresh-task restoration. Review/save/publish remains in environment settings.

## Remaining scope

The new engine is an internal library, not yet an API/UI or scheduled simulator. Opening balances and market rules are explicit local Paper inputs, not authenticated capital or exchange capabilities. Single-account locking is not shared-capital risk or leased external dispatch ownership. Local source seals do not establish exchange finality. No Live orders or private transport occur.

Next: bounded read-only journal inspection and complete offline verification, then explicit control/risk gates before enabling commands or scheduling. Preserve existing engine versions, ledgers and snapshots.
