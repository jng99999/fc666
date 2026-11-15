# Add the official-source collector to the verified PAPER-only runtime.
ARG BASE_IMAGE=fc666-directional-test:closed-rules-recovery-20261116
FROM ${BASE_IMAGE}
COPY apps/api/directional_service.py /app/apps/api/directional_service.py
COPY core/paper/public_news.py /app/core/paper/public_news.py
ENV LIVE_TRADING=false
