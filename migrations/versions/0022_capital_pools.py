"""Immutable exclusive local capital pool membership."""
from alembic import op
import sqlalchemy as sa
revision='0022'
down_revision='0021'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('paper_capital_pools',sa.Column('pool_id',sa.String(128),primary_key=True),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False))
    op.create_table('paper_capital_members',
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),primary_key=True),
        sa.Column('pool_id',sa.String(128),sa.ForeignKey('paper_capital_pools.pool_id'),nullable=False),
        sa.Column('ordinal',sa.Integer(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('pool_id','ordinal',name='uq_paper_capital_member_ordinal'),
        sa.CheckConstraint('ordinal>=0 AND ordinal<100',name='ck_paper_capital_member_ordinal'))
    for table in ('paper_capital_pools','paper_capital_members'):
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM paper_capital_pools) OR EXISTS (SELECT 1 FROM paper_capital_members)')):
        raise RuntimeError('Cannot downgrade persisted local capital pool evidence')
    op.drop_table('paper_capital_members');op.drop_table('paper_capital_pools')
