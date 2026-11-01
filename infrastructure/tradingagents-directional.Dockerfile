# Build on the existing isolated account runtime; never installs model dependencies.
FROM fc666-directional-test:runtime-20261027
COPY apps/api/directional_service.py apps/api/tradingagents_research.py apps/api/ai_paper.py /app/apps/api/
COPY core/paper/directional.py core/paper/directional_store.py /app/core/paper/
COPY core/research /app/core/research
