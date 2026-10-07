# Hint-only portfolio risk observations v1

GET `/api/v1/portfolio/scenarios/{id}/risk?limit=8` (1..8) evaluates saved observations under `paper-portfolio-exposure-hints-v1`. The policy freezes the immutable scenario ID, definition hash, existing exact Decimal gross/asset thresholds, BTC/ETH asset scope, USDT quote, STRICT_GT comparison and HINT_ONLY mode. Its hash binds these fields. It is a versioned retrospective interpretation of saved reports, not proof that a new policy was active during original captures. Original scenario/report/continuity/sampled versions remain unchanged. No new migration, mutable risk configuration, scheduler or trading gate is introduced.

Each envelope is reproduced through continuity-v1 in a bounded repeatable-read window. GROSS_WEIGHT, ASSET_WEIGHT:BTC and ASSET_WEIGHT:ETH produce BREACH, NOT_TRIGGERED or UNAVAILABLE. Equality does not trigger. Absent assets are zero only for a COMPLETE valuation with positive equity. Missing valuation or nonpositive equity suppresses all ratios, including absent-asset ratios. Assets are already consolidated by base in the original valuation; duplicate selected accounts are rejected by original reconstruction.

Point status is UNKNOWN if any rule cannot be computed or the saved worker warning says health was unconfirmed; otherwise HINTS_PRESENT for a threshold breach or saved operational hint; otherwise NO_CONFIGURED_HINTS. No status means complete risk acceptance. ENTRY_HALTED is a descriptive operational hint, not a new breach or trade action. Saved account and price clocks, original unavailable reasons and worker/entry-halt hints are retained. Current health is never inferred from an old snapshot.

ENTERED_BREACH and LEFT_BREACH events are emitted only for differing evaluated states of adjacent comparable points. The first window observation is BASELINE. Every failed continuity link is RESET, retains all reasons, and emits no transition. No missing minute, unavailable point, same-minute duplicate, worker warning, definition/ledger/source change or truncated window can imply a risk clearance. Repeated breaches do not emit another entry event. Exposure state changes are observed at saved points, not claimed to occur at an exact intervening instant.

The UI requests an explicit read, displays policy/version/hash/frozen limits, exact rule ratios and point context, and removes stale results on failure or scene changes. Complete compact JSON is capped at 32 MiB and includes all original input reports. Offline verification:

```sh
uv run --frozen python -m scripts.verify_portfolio_risk export.json
```

Verification reproduces original reports, lineage, rules, statuses, events and policy hash. It establishes internal consistency, not source authenticity, identity or full historical completeness. No orders, balances, controls, account risk limits, current rules or persisted snapshots are written. Continuous drawdown gates, correlated portfolio risk beyond base-asset aggregation, shared capital, OMS, multiuser auth, notifications and Live remain incomplete.
