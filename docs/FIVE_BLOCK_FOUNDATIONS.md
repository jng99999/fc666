# Five-block local foundations, schema0025

This delivers one coordinated local development batch under FIVE_BLOCK_EXECUTION_PLAN.md. The five complete production blocks are still unfinished. No exchange credentials, cloud deployment identity or production target are configured. Private credentials must be provisioned securely by the target runtime; the HMAC signing secret needs a real local secret binding, not a proxy placeholder. Do not send keys in chat or commit them. No external order endpoint is implemented or enabled.

## OMS staged-unapplied inbox

POST `/api/v1/paper-requested/inbox-commands` requires independently configured STAGE_LOCAL_INPUT and explicit account scope. The strict body contains account/request IDs, expected financial revision, current owner/token, LOCAL_PAPER_OPERATOR_INPUT source and a bounded event. New staging requires a known controlled request and an unexpired current claim at both transaction checks. Source sequence may be0..1,000,000,000; widening the staging grammar does not widen the frozen financial event grammar. Shape, decimal and clock validation is syntactic; financial effect and venue origin remain unverified.

Account locking serializes staging and financial writes. An immutable receipt binds the original proposal, actual request hash, control checkpoint, claim hash and DB microsecond receipt time. Staging commits no financial event, changes no funding/financial revision, and never releases holds. STAGED_UNAPPLIED describes the original receipt checkpoint. An exact receipt retry acknowledges that original record even after later activity/lease expiry; it grants no new authority. Conflicting ID/sequence/content rejects. First ownership cannot be retrofitted onto old submitted history.

POST `inbox-captures` independently requires READ_LOCAL_INBOX; STAGE does not imply READ. Reads audit financial and historical request/control/claim/storage context, then show frozen receipts and derived current journal presence/conflict observations. A later application never rewrites the original staging receipt. The inbox does not automatically drain. It stages ordinary delayed/gapped local observations as well as overflow, rather than silently relabeling an already-applied event.

Limits are128 records/account,8KiB/receipt and1MiB aggregate stored evidence. SQL size checks precede loading. Refused/full inputs are not accepted and must be retained upstream. Immutability triggers prohibit UPDATE/DELETE; downgrade to0024 refuses a populated inbox. No eviction, archive deletion, rollover or guaranteed settlement is implied. Segmented journal application remains the next major dependency.

## Selected requested-account exposure

POST `exposure-captures` requires READ_REQUESTED_EXPOSURE for every selected account before storage. Select1..8 unique accounts. A repeatable-read transaction locks sorted accounts and audits complete financial histories. Canonical Binance BTC/ETH Spot rules and a common closed1m candle price are required, observed within75s; missing/noncanonical/stale quotes reject the whole report.

The report counts selected account cash once, exposes reserved/available cash and inventory, and aggregates current and pending-BUY asset quantities. Pending sells do not optimistically remove exposure. Equity is cash plus marked inventory; BUY cash holds are a subset, not extra money. Pool unallocated cash is deliberately excluded: this is selected allocated-account exposure, not a whole-pool valuation or execution risk authorization. Full frozen journal/quote evidence and a deterministic hash permit `core.portfolio.requested_exposure.verify` replay. Historical report verification is not current permission.

## Private read-only Spot boundary

`core.exchange.private_spot.ReadOnlySpot` provides fixed-host HTTPS GET queries for account, per-symbol open orders and a bounded trade page. Credentials are SecretStr-backed and redacted from repr/errors. The client signs a deterministic query and uses a5s recvWindow/timeout, refuses redirects, preserves TLS/proxy defaults and bounds response decoding at2MiB with duplicate/nonfinite JSON rejection. It validates identities, quantities, asset codes and duplicates. Normalized outputs explicitly remain unreconciled; a bounded page never claims complete history. No POST/DELETE order methods, server route, credential vault or real-account identity integration is added.

Signing, destination/method restriction, normalization, oversized/error/redirect handling are verified with fixture-only MockTransport. Actual authenticated exchange queries are unrun because credentials are absent. Fixed-host api.binance.com access must be provisioned through the supported network workflow when that operation is actually needed; current environment settings were not silently broadened. This client is a tested connector foundation, not secure multi-user account custody or live readiness.

## Production recovery foundation

`python -m scripts.backup_drill` exports a consistent PostgreSQL custom archive using a held pg_export_snapshot, stores it under ignored `.runtime/backups` with0600 files/0700 directory, and restores into a new UUID-named isolated database. Timescale pre/post restore hooks are used. The drill compares schema, every public table count and streamed canonical-row SHA256 digests, then drops only its isolated target. Archive SHA256 and evidence manifest are retained. It never restores over main or alters its financial data.

Two actual main snapshot/restore exercises passed; the strengthened final drill verified row contents and left no restore database behind. A separate populated fixture drill restored two financial events, a staged receipt, partial inventory and the retained303 cash hold with identical row digests. The helper supports only local compose main or validated fc666_test_ UUID sources. This proves local restore of the tested snapshot, not offsite retention, encryption/key management, disaster recovery objectives or production failover. Backups can contain sensitive data; retain the protected files as operational artifacts, not public fixtures.

The existing `/health/ready` and `scripts/dev_services.py status` provide dependency/engineering checks, separately from market health and trading permission. `.github/workflows/local-acceptance.yml` adds frozen installation, isolated storage, full tests/Alembic/typecheck/build, an isolated backup restore drill and cleanup on hosted CI. The workflow is configured; its remote execution is unverified. Local selected tests and typecheck are verified. Sustained load, alerts, fault domains and actual target deployment/rollback remain outstanding.

Before a future migration use a validated backup. Do not blindly downgrade after accepting inbox data: populated downgrade refuses. A production rollback requires a separately tested target procedure; these local scripts do not authorize overwriting existing databases or prove a release rollback.

## Causal walk-forward research

POST `/api/v1/research/walk-forward` uses the existing frozen stored-candle selection plus1..8 validated EMA/SMA parameter candidates, fixed train/test sizes and at most8 complete folds. Every candidate trains on the preceding window only; highest training total return wins, with declared grid-order ties. Each test run cold-starts its own cash/inventory/indicator/signal state. Test windows do not overlap. Prior completed test data may appear in later training windows, which is causal rolling training. Trailing incomplete bars are explicitly unused.

Full dataset, parameters, costs/rules, candidate training results, selected index and out-of-sample runs are exported within32MiB. No cross-fold compounded return is invented for separately funded tests. `python -m scripts.verify_walk_forward FILE` bounds input, rejects duplicate JSON keys and reproduces every selection/test result. Future test changes cannot change a fold's training/selection. Profitability/statistical validity is not claimed. Durable-job/UI integration, strategy sandbox, broader matching/data and AI research remain unfinished.

## Acceptance evidence

85 distinct selected cases passed across new foundations, backtest, holdout, capacity, health HTTP, queued holdout jobs and storage integration. This includes actual inbox SIGKILL/reopen before commit/after lost reply, concurrent identical/conflicting delivery, capacity/fences, immutable rows, populated downgrade refusal and empty roundtrip. All generated account balances and credentials are fixtures; main17 requested/pool tables remain empty. Alembic check reports no pending operations at0025. Frontend typecheck passed. Only the existing Starlette/httpx deprecation warning was observed. The full1072-case suite and remote CI/deployment were not run or claimed.

This batch needs no new Python/npm dependency or frontend build. Main operator token/accounts/actions/pools remain absent, Live false, and all five development services are ready. Startup draft persistence, environment publication and independent fresh-task restoration are separate steps.

## Owned inbox application

`POST /api/v1/paper-requested/inbox-application-commands` independently requires
`APPLY_LOCAL_INPUT`. It identifies an immutable staging ordinal and SHA256,
current financial/control revisions, and the current owner/token. Under the
account lock it audits staging and financial history, requires the next sequence,
and applies through the existing source/journal transaction with server health
checks. Events, source receipts and dispatch evidence commit together. Original
staging receipts stay `STAGED_UNAPPLIED`; captures report journal presence.

Retries require current unexpired ownership and current revisions, and return
original source receipts. An independently delivered matching source event may
be acknowledged; this response does not claim that inbox application originally
created it. Unlabeled history cannot acquire retrospective source provenance.
Capacity and sequence bounds remain unchanged: overflow staging is retained but
cannot yet be applied beyond the frozen journal limit. Segmented journal storage,
automatic draining and archives remain outstanding. Default application grants
are absent; this feature performs local Paper processing only.

Validation: 50 distinct selected cases passed (49-case inbox/foundation/capacity/
health regression run plus the additional enrolled-health rollback case).
Alembic check found no upgrade operations; five development services were ready
following API restart. Full suite and remote CI were not run for this increment.
