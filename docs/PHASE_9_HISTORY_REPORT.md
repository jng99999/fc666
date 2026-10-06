# Phase 9 persisted scenario and valuation history acceptance

Scope: immutable named independent Paper scenarios and full valuation snapshots. Schema head 0009; no formula-version change, dependency, secret, network domain or service addition. Live remains disabled.

Twelve new PostgreSQL tests cover frozen definitions; exact idempotent replay; conflict and capacity; concurrent creation/capture; failure rollback and recovery; UPDATE/DELETE rejection on both tables; API validation, pagination and status filtering; unavailable totals remaining null; corrupted storage rejection; and offline envelope tampering detection. The prior 236 tests remain in the full suite (248 total).

TypeScript and production build pass. Alembic reports no pending model operations. Tested startup preserves local config and volumes, migrates to 0009, and starts API, frontend, research, market and Paper workers with readiness checks.

`tests.browser_portfolio_history` uses an existing real stopped account and current public source data. It commits scenario/snapshot POSTs but drops acknowledgement only in the test browser, then explicitly retries the same request after refresh. Both return the original records; only one snapshot is present. Export equals the original server envelope and reproduces offline. Read after refresh preserves report hash. At 393px there is no horizontal overflow and no JavaScript error. The previous BTC/ETH browser valuation regression also passes with COMPLETE real closed-minute pricing and exact export reproduction.

Browser-created scenarios/snapshots are actual persistent research records; they are retained under the same immutability policy. No data or live exchange accounts were fabricated. An unavailable quote may validly produce an UNAVAILABLE saved report. No continuous return or drawdown claim is made.

Next: define comparable history segments, missing observation intervals, changed source/rules/account clocks and independent-capital assumptions before deriving historical analytics. Full OMS/Risk/shared capital/auth and Live remain incomplete.
