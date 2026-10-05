"""Persist immutable historical replay snapshots and optimistic cursor versions."""
from alembic import op
import sqlalchemy as sa
revision='0005'
down_revision='0004'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('replay_sessions',
        sa.Column('session_id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('snapshot',sa.JSON(),nullable=False),
        sa.Column('cursor',sa.Integer(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.CheckConstraint("cursor >= 0 AND cursor <= json_array_length(snapshot->'dataset')",name='ck_replay_cursor'),
        sa.CheckConstraint('revision >= 0',name='ck_replay_revision'))


def downgrade():
    op.drop_table('replay_sessions')
