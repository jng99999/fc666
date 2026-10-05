"""Persist bounded historical paper accounts and their confirmed ledger."""
from alembic import op
import sqlalchemy as sa
revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('paper_sessions',
        sa.Column('session_id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('ledger', sa.JSON(), nullable=False),
        sa.Column('cursor', sa.Integer(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('halt_at', sa.Integer(), nullable=True),
        sa.CheckConstraint("cursor >= 0 AND cursor <= json_array_length(snapshot->'dataset')", name='ck_paper_cursor'),
        sa.CheckConstraint('revision >= 0', name='ck_paper_revision'),
        sa.CheckConstraint('halt_at IS NULL OR (halt_at >= 0 AND halt_at <= cursor)', name='ck_paper_halt'))


def downgrade():
    op.drop_table('paper_sessions')
