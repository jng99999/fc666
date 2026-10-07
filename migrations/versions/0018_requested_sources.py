"""Immutable local input receipts; old events remain explicitly unlabeled."""
from alembic import op
import sqlalchemy as sa
revision='0018'
down_revision='0017'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('requested_paper_events',sa.Column('source_version',sa.String(64),nullable=True))
    op.create_table('requested_paper_sources',
        sa.Column('request_id',sa.String(64),primary_key=True),sa.Column('sequence',sa.Integer(),primary_key=True),
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),nullable=False),
        sa.Column('event_id',sa.String(128),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.ForeignKeyConstraint(['request_id','sequence'],['requested_paper_events.request_id','requested_paper_events.sequence']))
    op.execute('CREATE TRIGGER requested_paper_sources_immutable BEFORE UPDATE OR DELETE ON requested_paper_sources FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM requested_paper_sources) OR EXISTS (SELECT 1 FROM requested_paper_events WHERE source_version IS NOT NULL)')):
        raise RuntimeError('Cannot downgrade persisted local source evidence')
    op.drop_table('requested_paper_sources');op.drop_column('requested_paper_events','source_version')
