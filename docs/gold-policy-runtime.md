# Gold paper runtime model contract (frozen draft for integration)

2026-10-22. Runtime source: `core/gold/policy_runtime.py`.

No production model is registered. This is a deployment evaluator contract, **not** a claim that research v1/v2 is qualified. Native v1 (`gold-ppo-target-v1`, float32) and native v2 (`gold-ppo-input-ablation-v2`, float32) are explicitly unsupported. A candidate cannot be relabelled without evaluating and qualifying this exact deployed policy. The explicit f64 deployment v2 schema below now supports the frozen A/B/C scaler contract read from research `core/gold/rl_experiments/__init__.py`; no scales or feature subsets are guessed/defaulted.

## Actor and numerics

Protocol/actor `version`: `gold-policy-json-f64-v1`. Observation version: `gold-hourly-observation-f64-v1`. JSON shape matches the local research exporter structurally: `version`, ordered `features`, ordered `actions`, `layers`. `features` must equal the ordered list below; `actions` must equal `["0", ".25", ".5"]`. Layers are `{"kind":"linear","weight":[output][input],"bias":[output]}` or exactly `{"kind":"tanh"}`. No pickle, ZIP, dynamic imports, torch, numpy, upload, external path, or unsupported opcode.

The pure evaluator is `validate_actor(actor)` then `infer(actor, observation, time.monotonic()+deadline_seconds)` in the source above. Python binary64 floats, each linear output `math.fsum(w*v for w,v in zip(row,input))+bias`, Python `math.tanh`. It **does not reproduce float32 Torch/NumPy accumulation**. All scalar JSON weights must be finite non-bool int/float with abs <= 1e6; 1..8 layers, width <=128, <=50000 parameters, input width10, output width3. Non-finite input/intermediate/output, timeout or invalid action rejects without fallback.

`MIN_LOGIT_MARGIN = 1e-6` (absolute, aligned with the research audit's predeclared threshold). Rank logits descending. If top-minus-second <= `1e-6`, reject `LOW_LOGIT_MARGIN`; **ties abstain**, there is no silent first-action fallback. Otherwise return the unique top action index. Runtime deadline defaults to100ms and is checked per linear output row plus after evaluation. A refused prediction consumes that closed hour; it must be counted as an abstention in research rather than converted to cash/EMA/model action. This supersedes the earlier draft relative1e-7 rule before any candidate was registered/audited.

## Ordered observation

Pure research helper: `observation_values(bars, cash_decimal_string, quantity_decimal_string, peak_decimal_string) -> (list[float], new_peak_string)`. This method has no source relabelling or online clock. `bars` are already validated chronological closed `core.gold.history.Bar` objects; use >=100 preceding observed hourly bars, not synthesized rows. Online `observe()` separately reparses every bar, rejects duplicates/unordered/partial bars, enforces the same Swissquote source, freshness and forward activation boundary.

Let last index be i, C=last mid close, H=3600000ms, r_j=log(float(C_j/C_(j-1))) for j=i-19..i. Population std uses `mean=math.fsum(r)/20`, `sqrt(math.fsum((r-mean)**2)/20)`.

1. `return_1 = log(float(C_i)/float(C_(i-1)))`
2. `return_4 = log(float(C_i)/float(C_(i-4)))`
3. `return_20 = log(float(C_i)/float(C_(i-20)))`
4. `range_20 = float((max(high[i-19:i+1])-min(low[i-19:i+1])) / C_i)` using Decimal arithmetic before float conversion
5. `volatility_20 = population std above`
6. `spread_fraction = float((ask_close-bid_close)/C_i)` using Decimal division
7. `elapsed_hours_log = log1p((start_i-start_(i-1))/H)`; a gap stays a gap, no forward/backward fill
8. `cash_fraction = float(cash/equity)`
9. `exposure = float(quantity*last_bid_close/equity)`
10. `drawdown = float((new_peak-equity)/new_peak)`

`equity=cash+quantity*last_bid_close`, `new_peak=max(previous_peak,equity)`, initially peak=initial_cash. Nonpositive equity rejects. Each feature must be finite, then explicitly clipped to[-10,10]. **No implicit normalization, float32 conversion, or missing-feature default.** The helper explicitly sets a local Decimal precision28 for the calculations, independent of caller context. Runtime trade accounting separately uses precision50. Unscaled actor v1 only accepts its exact four-field envelope, so a scaler accidentally attached to v1 is rejected rather than ignored.

## Explicit deployment v2 (A/B/C frozen preprocessing)

`version = gold-policy-ablation-f64-v2`; the raw `feature_version` remains `gold-hourly-observation-f64-v1`. Actor requires the base four fields plus:

- `input:"raw_f64_observation"`, `arm:"A"|"B"|"C"`, `scaler` (null for A, explicit frozen object for B/C).
- `arithmetic:"python_f64_fsum_tanh_margin_1e-6"`, `min_logit_margin:0.000001`.
- `native_actor_sha256` (byte hash of the original native f32 export), `dataset` (64hex frozen research dataset), integer hour-aligned `train_end`, integer `seed`, positive integer `timesteps`.
- `weights_sha256 = ledger.digest(actor["layers"])`. Actor byte hash separately binds all preprocessing and provenance metadata; manifest protocol must equal actor version.

Scaler shape is the existing frozen research payload: `mean` and `std` seven-element finite non-bool numeric lists; every std strictly positive; ordered `features` equal first7 feature names; integer `ddof:0`; `clip:[-10,10]`; `fit:"training_decision_observations_only"`; integer `samples`2..50000, `first_observed_at`, `last_observed_at`, `train_end`, with `0<=first<last<train_end` and scaler cutoff exactly actor cutoff. No sample refit, zero-variance substitution or implicit scaler is accepted. Qualification still has to prove those statistics came from the declared training prefix; JSON metadata alone does not prove that fact.

Pure `preprocess(actor, raw_observation)` is called **inside infer**, exactly once. Input is a finite10-vector (bool rejected), clipped[-10,10]. A keeps it unchanged and forbids a non-null scaler. B/C compute each market coordinate `(raw[j]-mean[j])/std[j]` in Python f64, reject any nonfinite scaled coordinate, then clip[-10,10]; C validates/computes the transform first and then zeros all seven market coordinates. Account coordinates7..9 remain unchanged. `infer(actor, raw_observation, deadline)` performs preprocessing itself; **do not pass externally scaled values into infer**. `validate_actor(actor)` validates frozen metadata and weights first. Runtime receipts hash both raw and preprocessed observations.

Research should load this complete controlled module (and record its SHA256) rather than AST-extract only `infer`: it deliberately calls shared `preprocess` and constants. A standalone AST snapshot of only infer lacks its preprocessing and abstention semantics. For margin diagnostics that intentionally continue an unqualified first-argmax trajectory, keep a separately labelled research-only diagnostic; the deployable evaluator raises `Rejected('LOW_LOGIT_MARGIN')`. No eligible candidate may have unaccounted refusals or native/deployed action mismatches. The source-transfer boundary (HistData research versus Swissquote execution) remains a separate explicit qualification item.

## Online data/execution

The live source is only `swissquote.public:AT:AT:standard:XAU/USD`. It aggregates raw same-source10s samples directly into1h paired bars: first/last observed sample within20s of hour bounds, max adjacent gap30s, no invented volume, missing-hour fill, or HistData/1m-feature stitching. It reads the latest100000 raw observed samples before the current boundary and retains the last100 complete observed hours, with **no calendar-hour cutoff**, so weekend closures and sparse incomplete hours do not discard legal older warmup bars. This bounded prefix is cached by closed-hour boundary plus an indexed latest-preboundary sample watermark across account decisions; unchanged samples cause no repeated per10s historical aggregation. A final late-but-fresh closing sample invalidates the cache and may retry a prior WAITING attempt; one actual inference attempt still consumes that close permanently. If even this bounded observed sample history lacks100 valid bars, wait explicitly. A closing timestamp must be within120s of runtime clock. Creating/resuming requires the next forward hourly boundary, so an older latest bar cannot generate an order. Lack of actual hourly history correctly returns WAITING; historical HistData is never substituted into the Swissquote live stream.

Action maps to target **equity exposure**0/.25/.5, with registered allocation exactly.5. At commit, use a fresh server-owned current bid/ask (existing <=60s freshness and <=5s forward clock tolerance), require quote.ts>=hour close, and enforce account max spread. Desired ounces=floor_to_0.01((cash+held*bid)*target/(ask+slippage+commission)); trade only desired-held, capped at10oz and available cash/inventory. Use existing ledger.fill accounting and costs. No leverage/short. Step-sized HOLD produces a receipt; no order. Prediction occurs outside SQLite transaction. Commit rechecks owner/account revision, status/control generation, registry qualification/hash/context/expiry and clock; PAUSE/STOP invalidates in-flight outputs. One decision per closed hour; idempotent commands and restart never replay missed hourly orders.

## Registry/qualification boundary

Default `GOLD_POLICY_ENABLED=false`, empty registry. Optional operator directory `GOLD_POLICY_REGISTRY` contains `<model_id>.json` (IDs `[A-Za-z0-9_-]{1,64}`). Manifest fields: `protocol` (one of the two explicit f64 versions), `feature_version`, `source`, `approved_for_paper`=true, integer `valid_until` ms, `account_config`, `actor_file`, `actor_sha256`, `qualification_file`, `qualification_sha256`. Artifact files must be local `.json` basenames, no symlinks/traversal, <=1MB and byte-SHA256 exact. Manifest <=32KiB. Each request reloads and hashes, including before fill, so removals/changes revoke execution. Files are controlled by service operators; no HTTP registration, upload or eligibility override exists.

`account_config` pins all five decimal fields `initial_cash`, `allocation`, `max_spread`, `slippage`, `commission` (normalized decimal strings). Context hash is `ledger.digest({"protocol":actor["version"],"feature_version":FEATURE_VERSION,"source":SOURCE,"account_config":money_config(config)})`. Qualification JSON requires `eligible`=true, `reasons`=[], `actor_sha256` exact, `context_hash` exact and `synthetic_fixture`=false. This is a **server-reviewed deployment attestation** backed by research evidence; a boolean supplied by a browser is never accepted. Full research admission (all seeds/costs, nondegenerate behavior, returns/drawdown) and native-vs-deployed action differences must be audited before an operator writes this attestation. A denied v1/v2 report must stay denied. `Registry(...,allow_fixtures=True)` exists only for an explicit in-process test harness; app factory never enables it.

Research handoff must include exact actor byte hash, exact protocol/features/actions, explicit scale semantics, native float32 vs this deployed f64 action mismatch counts and low-margin abstention counts, plus all admission results under the deployed evaluator. A f64 policy passing unit fixtures is not a qualified trained policy or profitability evidence.

## API for frontend/integration

All owner-scoped under existing HttpOnly browser cookie; live trading remains hard-disabled at app startup.

`GET /api/v1/gold/policy/status` is public read-only (no account/cookie/actor/path data). Exact response shape:

```json
{
  "enabled": false,
  "trading_enabled": false,
  "source": "swissquote.public:AT:AT:standard:XAU/USD",
  "required_bars": 100,
  "complete_bars": 0,
  "latest_close_at": null,
  "warmup_status": "WAITING_HISTORY",
  "status": "DISABLED",
  "models": [],
  "checked_at": 1792500000000
}
```

This JSON is a schema example, not a live observation. Integers are milliseconds for time fields; `latest_close_at` is integer or null. `complete_bars` counts actual complete same-source hourly bars retained in the cache (0..100). `warmup_status` is `WAITING_HISTORY` (<100), `WAITING_FRESH_CLOSED_HOUR` (100 but latest close not within0..120000ms), or `READY`. Top `status` precedence is `DISABLED` when operator flag off, then `NO_QUALIFIED_MODELS` when no currently hash/contract/qualification/expiry-valid registration, then the exact `warmup_status`. These fields stay separate so disabled/no-model/data shortage are visible independently.

Each available model has **only** `{"id":string,"protocol":string,"feature_version":string,"valid_until":integer,"account_config":{"initial_cash":decimal_string,"allocation":decimal_string,"max_spread":decimal_string,"slippage":decimal_string,"commission":decimal_string}}`. It lists no weights, filenames, directory, qualification evidence or account secrets. Invalid/unqualified/expired native actors are absent, never relabelled available. Model metadata is verified from local controlled files at request time; hourly count reuses the shared `HourlyFeed` cache and does not rescan samples per HTTP poll. Frontend should disable account creation and DECIDE unless enabled, model present and warmup READY; show the explicit reason. Even READY does not override the account's next-forward-hour activation boundary or an already-consumed close.

- `GET /api/v1/gold/policy/accounts`: `{accounts,enabled,trading_enabled:false}`, creates browser session if absent.
- `POST /api/v1/gold/policy/accounts`: five decimal config fields plus `mode:"POLICY"`, `model_id`, `experimental:true`, UUID `request_id`. Operator flag, exact context and full reviewed registration required.
- `GET /api/v1/gold/policy/accounts/{UUID}?before=N`: state plus latest100 immutable `events` and `next_before`.
- `POST /api/v1/gold/policy/accounts/{UUID}/command`: UUID `request_id`, integer `expected_revision`, action `DECIDE|PAUSE|RESUME|STOP`. No observation/weight/target input. STOP is terminal; cash/inventory preserved. The server10s collector also attempts the current observed hourly prefix for active policy accounts; a WAITING attempt may retry only when a new closing-prefix watermark arrives, never after an actual inference consumed the close.

Existing MANUAL/SMA20 create/command contracts are preserved; their account list excludes POLICY. Old generic read/events returns404 for POLICY so old mode enums do not change. New policy accounts share existing environment safety quotas (8 accounts per owner /256 total). Status/error codes: disabled/unregistered/unqualified/context mismatch etc422; stale revision/control, consumed hour, stopped account409; missing session401; wrong owner/account404. Decision-time refusal yields a durable `POLICY_DECIDE` receipt with `details.reason`, no fill; it is a handled safe result, not a successful order. Receipts bind model id/hash, feature version, context hash, observation hash, source-bars hash, close_at and optional simulated fill. Lack of history keeps phase WAITING. UI must display receipt reason and actual filled quantity, not infer success from HTTP200.

创建请求只在账本 `BEGIN IMMEDIATE` 事务内识别幂等：必须是唯一的同 owner 原始 `CREATED` 回执（seq=1、previous_hash=null），原配置完整匹配本次受信配置，持久 request_hash 同时匹配原配置与本次配置的 canonical digest。匹配到命令 UUID、不同摘要/配置或跨账户歧义时返回409；不会因当前账户恰好使用同一模型/配置而承认创建成功。原创建 UUID 跨小时、账户已暂停后重试仍返回同一账户的当前状态，不新增事件；其他 owner 的 UUID 不参与冲突或泄露。

## Immutable request recovery

`GET /api/v1/gold/policy/requests/{request_uuid}` requires the existing browser session (no cookie401, invalid UUID422). It is a read-only owner-scoped lookup of committed immutable events on POLICY accounts; it never calls runtime enabled, registry, model validity or market/history providers. It remains usable after disable, revocation, expiry, pause/STOP or restart. It excludes legacy MANUAL/SMA20 events. A different owner receives the same missing response as an unknown UUID, with no foreign account/hash/config fields.

For one matching committed receipt, HTTP200 response fields are exactly:

- `found:true`, `request_id` (normalized UUID string), `account_id`, `seq` (integer event revision), `kind` (`CREATED|POLICY_DECIDE|POLICY_PAUSE|POLICY_RESUME|POLICY_STOP`).
- `event_hash` (SHA256 of canonical stored event payload), `request_hash` (SHA256 with scope below), `hash_scope` (`ACCOUNT_CONFIG|COMMAND_BODY`).
- `state`: the **original state attached to this event**, including its original account config/model binding; never substituted with the newest state.
- `details`: the original event details, including refusal reason or simulated fill for DECIDE.
- `account`: current account state from the **same SQLite read snapshot** as the matching event. It may have a later revision/status than `state`; concurrent commits after the read snapshot are observed on a later GET.

For `CREATED`, `hash_scope=ACCOUNT_CONFIG` and `request_hash=ledger.digest(state.config)`; this is **not** a hash of the HTTP create JSON. Clients compare the original money fields numerically (normalized Decimal strings may use exponent notation), mode and `state.config.policy.model_id`/protocol/model binding with the intended creation. For policy commands, `hash_scope=COMMAND_BODY` and hash is `ledger.digest({request_id: normalized_uuid, expected_revision: original_integer, action: original_action})` of the normalized validated API body. The server also independently reconstructs this three-field command using `expected_revision=seq-1` and the kind suffix to verify its stored hash. Ledger canonical JSON uses sort_keys=True, separators=(',',':'), ensure_ascii=True, UTF8. Clients must compare original UUID/account/kind/body hash before treating a found command as the intended operation; a found DECIDE with a rejection reason is a committed refusal, not a fill.

Missing HTTP200 response is exactly `{found:false, request_id: normalized_uuid, absence_proves_not_applied:false}`. This proves only that the read snapshot contains no committed matching owned POLICY receipt. The POST can still be in flight (including CREATE inside an uncommitted transaction, or DECIDE inference before its write transaction). **Never clear an unknown UUID, declare failure/success, or issue a fresh request ID based on this missing result.** Continue explicit read-only reconciliation. A subsequent POST422 caused by model expiry/revocation/disable likewise cannot resolve an earlier unknown POST; use this receipt endpoint and retain the original UUID/body until its outcome is established.

The ledger only guarantees UUID uniqueness within each account. If the same owner reused the same UUID across multiple POLICY accounts, recovery returns HTTP409 with detail `Request ID matches multiple policy accounts; inspect account histories`; it never picks an arbitrary account. This lookup conflict is also unresolved and does not authorize clearing the unknown operation or resending it. Users can inspect the known account histories. Identical UUIDs belonging to different owners remain fully isolated and resolve independently.

The handler begins an explicit read transaction, verifies each historical payload digest, account id/mode/revision, allowed event kind and the creation/config or command/body hash, then reads the current account through the existing integrity check. Integrity failure is an error response, never `found:false`. The endpoint adds no writes, migrations, broker access, new authorization or dependencies; responses retain Cache-Control:no-store.

## Validation status

Synthetic fixture tests are in `tests/test_gold_policy_runtime.py`; known-constant actors are intentionally not admission evidence. The real research deployment export `actor-A-7.json` has additionally passed this exact runtime's schema validator (protocol `gold-policy-ablation-f64-v2`), without registration or orders. This is format compatibility only. Real model promotion, complete research parity/admission results, real1h forward operation, integration CI and deployment remain separate parent-thread acceptance items. No server/real ledger was accessed or migrated.
