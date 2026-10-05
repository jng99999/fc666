"""Initial instruments and candle storage, TimescaleDB enabled."""
from alembic import op
import sqlalchemy as sa
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.create_table("instruments",
        sa.Column("instrument_id", sa.String(160), primary_key=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("native_symbol", sa.String(64), nullable=False),
        sa.Column("base", sa.String(32), nullable=False),
        sa.Column("quote", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("contract_type", sa.String(32)),
        sa.Column("settlement_currency", sa.String(32)),
        *[sa.Column(name, sa.Numeric(38,18), nullable=False) for name in
          ("tick_size", "quantity_step", "min_quantity", "min_notional", "contract_size")],
        sa.UniqueConstraint("exchange", "native_symbol", "market_type", name="uq_instrument_native"),
        sa.CheckConstraint("market_type IN ('SPOT','PERPETUAL')", name="ck_market_type"),
        sa.CheckConstraint("tick_size > 0 AND quantity_step > 0 AND min_quantity >= 0 AND min_notional >= 0 AND contract_size > 0", name="ck_instrument_rules"),
        sa.CheckConstraint("market_type <> 'PERPETUAL' OR (settlement_currency IS NOT NULL AND contract_type IS NOT NULL)", name="ck_contract"))
    op.create_table("candles",
        sa.Column("instrument_id", sa.String(160), sa.ForeignKey("instruments.instrument_id"), primary_key=True),
        sa.Column("timeframe", sa.String(4), primary_key=True),
        sa.Column("open_time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        *[sa.Column(name, sa.Numeric(38,18), nullable=False) for name in
          ("open", "high", "low", "close", "volume")],
        sa.Column("is_closed", sa.Boolean, nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.CheckConstraint("low > 0 AND low <= open AND low <= close AND high >= open AND high >= close AND volume >= 0", name="ck_candle_values"),
        sa.CheckConstraint("close_time > open_time", name="ck_candle_time"),
        sa.CheckConstraint("timeframe IN ('1m','5m','15m','1h','4h','1d')", name="ck_timeframe"))
    op.execute("SELECT create_hypertable('candles', 'open_time', if_not_exists => TRUE)")

def downgrade():
    op.drop_table("candles")
    op.drop_table("instruments")
    # The extension may be shared; do not drop it.
