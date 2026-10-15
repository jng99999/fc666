#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/uv}"
export npm_config_cache="${npm_config_cache:-${XDG_CACHE_HOME:-$HOME/.cache}/npm}"
export NEXT_TELEMETRY_DISABLED=1
uv run --frozen pytest -q
uv run --frozen alembic check
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
