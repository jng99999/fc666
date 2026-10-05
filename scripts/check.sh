#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR=/workspace/.cache/uv
export npm_config_cache=/workspace/.cache/npm
export NEXT_TELEMETRY_DISABLED=1
uv run --frozen pytest -q
uv run --frozen alembic check
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
