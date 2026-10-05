"""Durable research jobs and worker heartbeats."""
from alembic import op
import sqlalchemy as sa
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('research_jobs',
        sa.Column('job_id',sa.String(36),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),sa.Column('progress',sa.Integer(),nullable=False),sa.Column('cancel_requested',sa.Boolean(),nullable=False),sa.Column('attempts',sa.Integer(),nullable=False),
        sa.Column('lease_owner',sa.String(36)),sa.Column('lease_until',sa.DateTime(timezone=True)),sa.Column('request',sa.JSON(),nullable=False),sa.Column('snapshot',sa.JSON(),nullable=False),sa.Column('result',sa.JSON(none_as_null=True)),sa.Column('error',sa.String(160)),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",name='ck_research_status'),
        sa.CheckConstraint('progress BETWEEN 0 AND 100 AND attempts BETWEEN 0 AND 3',name='ck_research_progress'),
        sa.CheckConstraint("(status = 'SUCCEEDED' AND result IS NOT NULL AND progress = 100) OR (status <> 'SUCCEEDED' AND result IS NULL AND progress < 100)",name='ck_research_result'))
    op.create_index('research_jobs_queue_idx','research_jobs',['status','created_at'])
    op.create_table('research_workers',sa.Column('worker_id',sa.String(36),primary_key=True),sa.Column('heartbeat_at',sa.DateTime(timezone=True),nullable=False))

def downgrade():
    op.drop_table('research_workers');op.drop_index('research_jobs_queue_idx',table_name='research_jobs');op.drop_table('research_jobs')
