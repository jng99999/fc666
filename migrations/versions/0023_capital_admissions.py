"""Immutable mandatory admission evidence for pool member requests."""
from alembic import op
import sqlalchemy as sa
revision='0023'
down_revision='0022'
branch_labels=None
depends_on=None


def upgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM paper_capital_members m JOIN requested_paper_requests r ON r.account_id=m.account_id)")):
        raise RuntimeError("Cannot retrofit missing shared admission to existing pool requests")
    op.create_table('paper_capital_admissions',
        sa.Column('request_id',sa.String(64),sa.ForeignKey('requested_paper_requests.request_id'),primary_key=True),
        sa.Column('account_id',sa.String(128),sa.ForeignKey('paper_capital_members.account_id'),nullable=False),
        sa.Column('pool_id',sa.String(128),sa.ForeignKey('paper_capital_pools.pool_id'),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False))
    op.execute('CREATE TRIGGER paper_capital_admissions_immutable BEFORE UPDATE OR DELETE ON paper_capital_admissions FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM paper_capital_admissions)')):
        raise RuntimeError('Cannot downgrade persisted shared capital admission evidence')
    op.drop_table('paper_capital_admissions')
