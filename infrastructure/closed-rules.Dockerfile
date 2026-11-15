# Overlay the verified, already-deployed operator runtime; no dependency upgrade.
ARG BASE_IMAGE=fc666-directional-test:operator-20261102
FROM ${BASE_IMAGE}
COPY apps/api/directional_service.py /app/apps/api/directional_service.py
COPY core/paper/directional.py core/paper/directional_store.py core/paper/price_action_rules.py core/paper/rule_market.py /app/core/paper/
ENV LIVE_TRADING=false
