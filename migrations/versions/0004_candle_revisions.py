"""Audit confirmed authoritative REST corrections without losing prior values."""
from alembic import op
import sqlalchemy as sa
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('candle_revisions',sa.Column('revision_id',sa.String(36),primary_key=True),sa.Column('instrument_id',sa.String(160),nullable=False),sa.Column('timeframe',sa.String(4),nullable=False),sa.Column('open_time',sa.DateTime(timezone=True),nullable=False),sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),sa.Column('reason',sa.String(160),nullable=False),sa.Column('previous',sa.JSON(),nullable=False),sa.Column('revised',sa.JSON(),nullable=False))
    op.create_index('candle_revisions_lookup_idx','candle_revisions',['instrument_id','timeframe','open_time'])

def downgrade():
    op.drop_index('candle_revisions_lookup_idx',table_name='candle_revisions');op.drop_table('candle_revisions')
