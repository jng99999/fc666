#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR=/workspace/.cache/uv
python3 scripts/local_config.py
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock compose --env-file .env -f infrastructure/compose.yaml up -d --wait
uv run --frozen alembic upgrade head
.venv/bin/python scripts/dev_services.py start
