#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR=/workspace/.cache/uv
export npm_config_cache=/workspace/.cache/npm
export NEXT_TELEMETRY_DISABLED=1
python3 scripts/dev_services.py stop
uv sync --frozen
npm ci --prefix apps/web --no-audit --no-fund
python3 scripts/local_config.py
npm run build --prefix apps/web
