"""Bounded immutable staged-unapplied local input; no financial application."""
from alembic import op
import sqlalchemy as sa
revision='0025'
down_revision='0024'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('requested_paper_inbox',
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),primary_key=True),
        sa.Column('ordinal',sa.Integer(),primary_key=True),
        sa.Column('request_id',sa.String(64),sa.ForeignKey('requested_paper_requests.request_id'),nullable=False),
        sa.Column('event_id',sa.String(128),nullable=False),sa.Column('source_sequence',sa.BigInteger(),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('request_id','event_id',name='uq_requested_inbox_event'),
        sa.UniqueConstraint('request_id','source_sequence',name='uq_requested_inbox_sequence'),
        sa.CheckConstraint('ordinal>=0 AND ordinal<128',name='ck_requested_inbox_ordinal'))
    op.execute('CREATE TRIGGER requested_paper_inbox_immutable BEFORE UPDATE OR DELETE ON requested_paper_inbox FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM requested_paper_inbox)')):
        raise ValueError('Cannot discard staged-unapplied inbox evidence')
    op.drop_table('requested_paper_inbox')
