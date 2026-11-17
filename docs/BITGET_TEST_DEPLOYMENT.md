# Bitget test deployment

The test frontend stays on Vercel (`fc666-test`, root `apps/web`). The backend runs in its own Docker Compose project on the existing server, with its own TimescaleDB volume and Redis instance. The API binds only `127.0.0.1:18666`; the existing HTTPS site proxies the paths in `infrastructure/backend.nginx.conf`. The current public origin is `https://fc.yoyo89.sbs`.

## Server configuration

Create an untracked, permission-0600 `.env.backend` in the backend checkout. Generate a fresh random database password on the server; do not commit or print it. Required settings:

- `POSTGRES_USER=fc666` and `POSTGRES_DB=fc666` are configured in Compose.
- `POSTGRES_PASSWORD`: generated server-side.
- `DATABASE_URL`: PostgreSQL psycopg URL using the same password, host `postgres`, port `5432`, database/user `fc666`.
- `REDIS_URL=redis://redis:6379/0`
- `MARKET_PROVIDER=bitget`
- `LIVE_TRADING=false`
- `TRADING_MODE=PAPER`

No exchange API key, private account, or live order endpoint is required. Public data uses Bitget v2 REST snapshots and is pushed to browsers through this application's WebSocket gateway. This is periodic public snapshot collection, not exchange-level tick-by-tick streaming.

## Startup sequence

Run from the backend checkout:

```sh
docker compose -f infrastructure/backend.compose.yaml build api
docker compose -f infrastructure/backend.compose.yaml up -d --wait postgres redis
docker compose -f infrastructure/backend.compose.yaml run --rm --no-deps api alembic upgrade head
docker compose -f infrastructure/backend.compose.yaml run --rm --no-deps api python -m scripts.import_market --bars 1000
docker compose -f infrastructure/backend.compose.yaml up -d --no-build api market research paper
```

Preserve any existing database volume and account data. Apply migrations only after backing up existing databases. The initial deployment used a new isolated database. The current server directory is `/opt/fc666-test`.

Install the Nginx snippet through the existing site's extension directory after saving its prior configuration. Validate with `nginx -t` before reloading. Do not replace unrelated site configuration. Database/cache ports remain private to the Compose network.

## Vercel Production configuration for the test project

- `API_BASE_URL=https://fc.yoyo89.sbs`
- `NEXT_PUBLIC_MARKET_WS_URL=wss://fc.yoyo89.sbs/ws/v1/market`
- `NEXT_PUBLIC_MARKET_PROVIDER=bitget`
- `LIVE_TRADING=false`

These values require a new build. Deploy this branch's patched frontend; the original research-platform branch does not yet contain this adapter until the draft PR is merged.

## Acceptance evidence and limits

On 2026-10-15, the isolated deployment imported 1,000 real candles for each BTC/ETH × 1m/5m/15m/1h/4h/1d dataset, all with zero gaps. Daily candles use `1Dutc`; candle provenance is `bitget.public`. One existing candle was corrected only after two matching official observations, with its prior value retained in the revision table. Trade IDs remain strings to preserve integers larger than JavaScript's safe range.

Public health, schema, Redis, market, research, and Paper worker checks passed. Real WSS receipts covered both symbols' ticker, book, trades, and candles. A research job completed on 120 Bitget candles and result verification passed. One synthetic historical Paper account was created for acceptance; it does not connect to a live account. Invalid symbols return 422 and the live-order route is absent (404).

Focused tests: 45 adapter/model/worker cases plus 44 non-database market-quality cases passed; the six Bitget cases were rerun after the pagination fix. Local frontend build/typecheck and cloud builds passed. Full repository CI, mobile/device acceptance, prolonged soak testing, and disaster restoration are separate checks. An initial database dump was created and its archive listing was read; restoration was not claimed.

Runtime settings reject `LIVE_TRADING=true` and disallow `TRADING_MODE=LIVE`. Client labels are informational; server settings and absent execution functionality enforce the disabled state.

## Realtime acceptance follow-up

The `closed-bars-20261015` backend image removes an unintended one-minute delay for **new** closed Bitget candles. Live candles are never persisted; stored-bar corrections still require two matching observations and the existing 60-second correction gate. A newly closed bar can arrive after a valuation request, so a transient unavailable valuation is retained rather than substituting an older quote. Exchange corrections continue to invalidate accepted account history when appropriate.

The test deployment created a real Paper stream (`8d708fdb-f038-43a6-8c6f-dfbaf8b6db17`) from 120 real Bitget BTC 1m seed bars. Its background clock progressed, and the portfolio returned `COMPLETE` using the common closed-minute quote. Scenario `2cdc4ee8-ac21-4ec0-a229-f6614c265a27` and snapshot `b3dffabe-5d11-4324-a1b7-5db51f9a4cba` were saved, reloaded and retried with the original request IDs; retries returned the same records. They are synthetic test balances, not exchange accounts.

The frontend now exposes `/analysis`, using existing closed-only server indicators and public candle data; it rejects short, gapped, stale or mismatched inputs. `/research` groups market/strategy and funding/cost inputs, with an editable 2–8 variant batch table and optional JSON editor. AI providers and credentials remain unconfigured.

Backend changes were tested in an isolated container with 47 focused adapter/worker/model tests passing. Frontend calculation boundary tests, production build and typecheck passed. Test app containers were recreated with unchanged environment, init, command, mounts, resources, ports and security settings; database/cache volumes were preserved. No schema migration was required.
