"""One immutable local submission boundary per declared owned request."""
from alembic import op
import sqlalchemy as sa
revision='0020'
down_revision='0019'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('requested_paper_events',sa.Column('dispatch_version',sa.String(64),nullable=True))
    op.create_table('requested_paper_dispatches',
        sa.Column('request_id',sa.String(64),sa.ForeignKey('requested_paper_requests.request_id'),primary_key=True),
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),nullable=False),
        sa.Column('token',sa.Integer(),nullable=False),sa.Column('client_id',sa.String(64),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.ForeignKeyConstraint(['request_id','token'],['requested_paper_claims.request_id','requested_paper_claims.token']),
        sa.UniqueConstraint('client_id',name='uq_requested_paper_dispatch_client'))
    op.execute('CREATE TRIGGER requested_paper_dispatches_immutable BEFORE UPDATE OR DELETE ON requested_paper_dispatches FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM requested_paper_dispatches) OR EXISTS (SELECT 1 FROM requested_paper_events WHERE dispatch_version IS NOT NULL)')):
        raise RuntimeError('Cannot downgrade persisted local dispatch evidence')
    op.drop_table('requested_paper_dispatches')
    op.drop_column('requested_paper_events','dispatch_version')
