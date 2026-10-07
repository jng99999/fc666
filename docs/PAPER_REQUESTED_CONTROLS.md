# Controlled local Paper accounts and request-risk gates

Schema0016 adds `paper-requested-controls-v1` and immutable `paper-requested-risk-v1` policies to the independent explicit-quantity engine. Enrollment is explicit and opt-in through the internal library. Existing un-enrolled v1 accounts keep their former behavior and report `LEGACY_UNMANAGED`; this is not coverage for every Paper account. Closed-bar engines and historical records remain separate. No mutation API, UI, scheduler, human identity permissions or exchange transport is added.

## Enrollment, state and atomicity

`requested_controls.policy(account_id, limits)` requires every named limit explicitly. `enroll(engine, account_id, policy, command_id, created_at)` stores policy and ENROLL evidence with a one-way account marker in one transaction. It requires no unsealed legacy request and starts PAUSED. It does not change balances, financial revision or historical request receipts. No historic request is retrospectively authorized. Conflicting retry inputs or policy replacement fail.

`command` takes a stable command ID, expected control revision, action and aware clock. RESUME moves PAUSED/HALTED to ACTIVE; PAUSE moves ACTIVE/HALTED to PAUSED; HALT moves ACTIVE to HALTED; STOP closes any nonterminal state permanently. Repeating a command ID with exact original inputs is idempotent and returns the current control view; changed inputs fail. Control revision is independent of economic journal revision. Commands and economic acceptance share the account row lock, so concurrent stale commands cannot both succeed.

ACTIVE permits new local requests subject to policy. PAUSED/STOPPED deny new preparation and first SUBMIT. HALTED denies new BUY but allows permitted inventory-reducing SELL. Enrollment, policy, controls and gates are immutable; the enrollment marker cannot be cleared or replaced. A missing or corrupt declared record blocks financial inspection and further acceptance, without automatic repair.

For controlled accounts, PREPARE admission evidence commits with the full requested-quantity reservation. SUBMIT admission is checked again against the current controls and commits with the first source event. Historical inspection reconstructs the policy and control revision effective at each financial checkpoint, rather than applying a later pause retroactively. Exact committed retries do not dispatch or increment any revision. Controls limit1000 retained records, including ENROLL; ordinary commands reserve the final slot for STOP. Gate capacity follows at most100 requests and200 PREPARE/SUBMIT records.

## Explicit risk scope

The immutable policy requires:

- `allowed_sides`: unique BUY/SELL choices, possibly empty.
- `max_order_quantity`: positive full requested quantity cap.
- `max_request_notional`: positive requested quantity × frozen protective limit cap; it is not an executed-proceeds or mark-to-market exposure cap.
- `max_buy_inventory_quantity`: nonnegative inventory units after the entire requested BUY.
- `max_fee_rate`: nonnegative request fee ceiling, at most one.
- `min_cash_after_buy`: nonnegative cash remaining after reserving the entire BUY including the fee ceiling.

All values use the explicit execution contract's bounded decimal strings and260-digit arithmetic. Size limits apply to full demand, not a smaller prospective partial fill. Original market-grid, protective-price, inventory/cash and cumulative-fill checks still apply. Risk denial stores no new request, gate or economic effect; denied attempts are not an audit history in this version. Policy changes, shared capital, monetary portfolio exposure, drawdown, source-health/staleness checks and human authorization remain incomplete.

## Receipts after pause or stop

Once submission has been durably accepted, pause/stop cannot suppress UNKNOWN, ACK, cancel or fill evidence. Partial and late fills still settle exactly against the original request; cancellation acknowledgement retains remaining funding until a local source seal. Stopping does not claim venue cancellation or release funds. Exact SUBMIT redelivery after stopping returns the existing result without submitting again.

Stopping an unsubmitted prepared request likewise preserves its hold and prevents SUBMIT. There is currently no local-void terminal receipt for that request, and STOP cannot resume it. A separately versioned unsubmitted-finalization protocol is the next dependency; do not invent SUBMIT/REJECT/cancel evidence or clear reservations to bypass it.

## Read-only export and offline replay

GET `/api/v1/paper-requested/controls?account_id=...` returns `paper-requested-controls-export-v1`: the unchanged complete financial export, explicit coverage, policy, complete control/gate records, derived state/revision and content hash. Queries use the same bounded opaque account ID, no-store responses and generic404/409/503 errors as financial inspection. POST is405. The complete export is bounded to32MiB, with no pagination or pruning.

Run `.venv/bin/python -m scripts.verify_requested_controls FILE` offline. It first verifies the entire financial journal, then reconstructs control checkpoints/transitions and every PREPARE/SUBMIT decision. Altering and rehashing decisions cannot bypass replay. Hashes establish internal consistency, not source authenticity, identity permissions, external finality or freshness. CLI opens no database and rejects duplicate JSON object keys and oversized files. Original financial export v1 remains unchanged and valid.

Tests use isolated PostgreSQL fixtures only. Main tables remain empty; no fabricated product balances/trades. Startup remains `bash scripts/start.sh`, preserving .env/volumes and upgrading head0016 before starting all five services. No dependencies, secrets, domains or extra services. Live stays disabled.
