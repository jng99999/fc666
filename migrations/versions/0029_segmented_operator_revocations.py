"""Immutable database-scoped segmented operator revocations."""
from alembic import op
import sqlalchemy as sa
revision='0029'
down_revision='0028'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('segmented_operator_revocations',
        sa.Column('sequence',sa.Integer(),primary_key=True),
        sa.Column('command_id',sa.String(128),nullable=False,unique=True),
        sa.Column('subject_sha256',sa.String(64),nullable=False),
        sa.Column('ledger_id',sa.String(128),nullable=False),
        sa.Column('action',sa.String(64),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('subject_sha256','ledger_id','action',name='uq_segmented_revocation_scope'),
        sa.CheckConstraint('sequence>=0 AND sequence<1024',name='ck_segmented_revocation_sequence'),
        sa.CheckConstraint('octet_length(payload::text)<=4096',name='ck_segmented_revocation_bytes'))
    op.execute('CREATE TRIGGER segmented_operator_revocations_immutable BEFORE UPDATE OR DELETE ON segmented_operator_revocations FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM segmented_operator_revocations)')):
        raise ValueError('Cannot discard populated operator revocations')
    op.drop_table('segmented_operator_revocations')
