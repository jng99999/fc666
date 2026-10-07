"""Immutable local ownership claims and fenced event attribution."""
from alembic import op
import sqlalchemy as sa
revision='0019'
down_revision='0018'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('requested_paper_events',sa.Column('ownership_token',sa.Integer(),nullable=True))
    op.add_column('requested_paper_events',sa.Column('ownership_accepted_us',sa.BigInteger(),nullable=True))
    op.create_table('requested_paper_claims',
        sa.Column('request_id',sa.String(64),sa.ForeignKey('requested_paper_requests.request_id'),primary_key=True),
        sa.Column('token',sa.Integer(),primary_key=True),
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.CheckConstraint('token>0',name='ck_requested_paper_claim_token'))
    op.execute('CREATE TRIGGER requested_paper_claims_immutable BEFORE UPDATE OR DELETE ON requested_paper_claims FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM requested_paper_claims) OR EXISTS (SELECT 1 FROM requested_paper_events WHERE ownership_token IS NOT NULL OR ownership_accepted_us IS NOT NULL)')):
        raise RuntimeError('Cannot downgrade persisted ownership evidence')
    op.drop_table('requested_paper_claims')
    op.drop_column('requested_paper_events','ownership_token')
    op.drop_column('requested_paper_events','ownership_accepted_us')
