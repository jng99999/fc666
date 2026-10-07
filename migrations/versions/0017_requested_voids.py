"""Immutable local finalization for requests with no durable submission."""
from alembic import op
import sqlalchemy as sa
revision='0017'
down_revision='0016'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('requested_paper_voids',
        sa.Column('request_id',sa.String(64),sa.ForeignKey('requested_paper_requests.request_id'),primary_key=True),
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),nullable=False),
        sa.Column('command_id',sa.String(128),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('account_id','command_id',name='uq_requested_paper_void_command'))
    op.execute('CREATE TRIGGER requested_paper_voids_immutable BEFORE UPDATE OR DELETE ON requested_paper_voids FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():op.drop_table('requested_paper_voids')
