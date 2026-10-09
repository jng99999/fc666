"""Immutable opt-in whole-pool risk policies; no historical enrollment."""
from alembic import op
import sqlalchemy as sa
revision='0026'
down_revision='0025'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('paper_capital_risk_policies',
        sa.Column('pool_id',sa.String(128),sa.ForeignKey('paper_capital_pools.pool_id'),primary_key=True),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False))
    op.execute('CREATE TRIGGER paper_capital_risk_policies_immutable BEFORE UPDATE OR DELETE ON paper_capital_risk_policies FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM paper_capital_risk_policies)')):
        raise ValueError('Cannot discard mandatory pool risk policy evidence')
    op.drop_table('paper_capital_risk_policies')
