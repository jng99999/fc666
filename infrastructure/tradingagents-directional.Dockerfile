# Build on the existing isolated account runtime; never installs model dependencies.
FROM fc666-directional-test:runtime-20261027
COPY apps/api/directional_service.py apps/api/tradingagents_research.py /app/apps/api/
COPY core/research /app/core/research
