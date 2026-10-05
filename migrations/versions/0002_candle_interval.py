"""Enforce exact, aligned UTC intervals in the persistent candle store."""
from alembic import op
revision="0002"
down_revision="0001"
branch_labels=None
depends_on=None

def upgrade():
    op.create_check_constraint("ck_candle_interval","candles","extract(epoch from close_time-open_time) = CASE timeframe WHEN '1m' THEN 60 WHEN '5m' THEN 300 WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END AND mod(extract(epoch from open_time), CASE timeframe WHEN '1m' THEN 60 WHEN '5m' THEN 300 WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END) = 0")

def downgrade():
    op.drop_constraint("ck_candle_interval","candles",type_="check")
