# Vercel test frontend deployment

Import `jng99999/fc666`, select `codex/fc666-research-platform` and set Root Directory to `apps/web`. The main branch currently contains only the initial README; do not deploy main expecting the workstation. Use the committed npm lockfile (`npm ci`) and Next.js framework detection/build. `apps/web/vercel.json` records those settings. No exchange credentials or database URLs belong in frontend public variables.

Vercel hosts this Next.js frontend and its bounded HTTP proxy handlers. Full functionality requires a separately hosted, reachable FC666 API, PostgreSQL, Redis and long-running market/research/paper workers. Configure the server-only `API_BASE_URL` to that API's HTTPS origin before building/deploying; loopback addresses in the Codex cloud environment are unreachable from Vercel. Without a backend, the frontend is a layout preview and API-dependent functions report unavailable; no fixture data should substitute for real services.

The existing `/stream` rewrite does not establish Vercel support for persistent WebSocket proxying. Choose and verify a supported direct browser-to-backend WebSocket endpoint, its origin restrictions and TLS before claiming live-market functionality on the public deployment. Research/Paper mutations additionally require appropriate public-site access controls and backend admission limits before broad access. Live remains disabled.

Deployment acceptance: obtain the actual deployment URL, verify terminal/research pages load and browser scripts run, then separately check API health, market freshness, WebSocket connectivity and queued research completion against the deployed backend. A local build is frontend readiness evidence, not an actual Vercel deployment or full-platform production acceptance.

Publishing requires a Vercel connection/tool or securely provisioned runtime credentials and project/team selection. Never send tokens in chat or commit them. Current preparation does not create a project, deploy, or claim a public URL.
