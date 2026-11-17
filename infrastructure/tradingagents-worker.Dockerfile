FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY infrastructure/tradingagents-worker-lock.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt && useradd -u 10001 -m worker
COPY apps/__init__.py apps/tradingagents_worker.py /app/apps/
COPY core /app/core
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 LIVE_TRADING=false
CMD ["uvicorn", "apps.tradingagents_worker:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
