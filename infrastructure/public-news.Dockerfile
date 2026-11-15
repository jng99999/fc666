# Add the official-source collector to the verified PAPER-only runtime.
ARG BASE_IMAGE=fc666-directional-test:official-news-settled-20261116
FROM ${BASE_IMAGE}
COPY apps/api/directional_service.py /app/apps/api/directional_service.py
COPY core/paper/public_news.py core/paper/rule_market.py core/paper/nyfed_calendar.py /app/core/paper/
ENV LIVE_TRADING=false
