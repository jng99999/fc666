FROM fc666-directional-test:ai-paper-20261102
RUN uv pip install --python /app/.venv/bin/python --no-cache cryptography==50.0.2
COPY apps/api/directional_service.py apps/api/tradingagents_research.py apps/api/ai_paper.py apps/api/operator_settings.py /app/apps/api/
COPY core/security /app/core/security
COPY core/research/market_evidence.py /app/core/research/
