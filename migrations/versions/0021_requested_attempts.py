"""Immutable local failure-assessment attempts; no remote send evidence."""
from alembic import op
import sqlalchemy as sa
revision='0021'
down_revision='0020'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('requested_paper_attempts',
        sa.Column('request_id',sa.String(64),sa.ForeignKey('requested_paper_dispatches.request_id'),primary_key=True),
        sa.Column('ordinal',sa.Integer(),primary_key=True),
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),nullable=False),
        sa.Column('attempt_id',sa.String(128),nullable=False),sa.Column('token',sa.Integer(),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.ForeignKeyConstraint(['request_id','token'],['requested_paper_claims.request_id','requested_paper_claims.token']),
        sa.UniqueConstraint('request_id','attempt_id',name='uq_requested_paper_attempt_id'),
        sa.CheckConstraint('ordinal>=0 AND ordinal<16',name='ck_requested_paper_attempt_ordinal'))
    op.execute('CREATE TRIGGER requested_paper_attempts_immutable BEFORE UPDATE OR DELETE ON requested_paper_attempts FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM requested_paper_attempts)')):
        raise RuntimeError('Cannot downgrade persisted local attempt evidence')
    op.drop_table('requested_paper_attempts')
