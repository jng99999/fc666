"""Durable real-time closed-bar paper observations and worker readiness."""
from alembic import op
import sqlalchemy as sa
revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('paper_streams',
        sa.Column('session_id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('snapshot',sa.JSON(),nullable=False),
        sa.Column('observations',sa.JSON(),nullable=False),
        sa.Column('ledger',sa.JSON(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('halt_at',sa.Integer(),nullable=True),
        sa.Column('feed',sa.JSON(),nullable=False),
        sa.CheckConstraint("json_array_length(snapshot->'dataset') + json_array_length(observations) <= 1000",name='ck_stream_size'),
        sa.CheckConstraint('revision >= 0',name='ck_stream_revision'),
        sa.CheckConstraint("status IN ('RUNNING','PAUSED','STOPPED','BLOCKED','LIMIT_REACHED')",name='ck_stream_status'),
        sa.CheckConstraint('halt_at IS NULL OR (halt_at >= 0 AND halt_at <= json_array_length(observations))',name='ck_stream_halt'))
    op.create_index('paper_streams_active_idx','paper_streams',['status','created_at'])
    op.create_table('paper_workers',sa.Column('worker_id',sa.String(36),primary_key=True),sa.Column('heartbeat_at',sa.DateTime(timezone=True),nullable=False))


def downgrade():
    op.drop_table('paper_workers')
    op.drop_index('paper_streams_active_idx',table_name='paper_streams')
    op.drop_table('paper_streams')
