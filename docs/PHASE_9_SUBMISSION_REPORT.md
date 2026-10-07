# Phase 9 — fenced simulated submission acceptance

Implemented isolated durable leases, strictly increasing fencing tokens, one immutable simulated dispatch and query-only unknown-result recovery. Stable client identity and original dispatch survive owner changes and lost replies. Expired/stale owners cannot submit/deliver; expiry during mutation rolls back. Legacy unowned transcripts cannot acquire retrospective ownership. Public unfenced append cannot mutate an enrolled request.

Full backend regression:446 tests passed, with one existing Starlette/httpx deprecation warning. The final missing-lease bypass guard and added case were then verified in the58-test focused suite. Alembic check reports no new operations; all five owned services are functionally ready with trading disabled.

Focused acceptance:58 tests passed (20 new submission cases,18 durable fault-adapter cases,20 pure reconciliation cases). Tests use actual independently launched Python processes and SIGKILL before and after dispatch commit, with reopen/retry checking one retained local result. Concurrent owner selection, concurrent submission, immutable dispatch, missing/corrupt evidence, token rewind/deletion and within-transaction expiration are covered.

No production ledger, data, schema, dependency, credentials, network hosts, service list or frontend changes. Existing production schema remains0013; frontend rebuild/browser rerun is unnecessary for this isolated Python-only change. There are no private calls or real orders. No exchange authenticity, distributed fencing or machine power-loss acceptance is claimed. Owner strings are not user authentication.

Next target: versioned contract connecting owned simulated submission to actual account economic acceptance, before shared-capital risk or private exchange integration. Unknown outcomes currently query local evidence only and never authorize resubmission. Continue under the existing mandate; routine work requires no repeated confirmation.
