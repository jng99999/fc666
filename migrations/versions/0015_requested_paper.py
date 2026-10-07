"""Separate explicit requested-quantity Paper journal and account cache."""
from alembic import op
import sqlalchemy as sa
revision='0015'
down_revision='0014'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('requested_paper_accounts',
        sa.Column('account_id',sa.String(128),primary_key=True),
        sa.Column('opening',sa.JSON(),nullable=False),sa.Column('opening_sha256',sa.String(64),nullable=False),
        sa.Column('current',sa.JSON(),nullable=False),sa.Column('active_request_id',sa.String(64),nullable=True),
        sa.Column('revision',sa.Integer(),nullable=False))
    op.create_table('requested_paper_requests',
        sa.Column('request_id',sa.String(64),primary_key=True),
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),nullable=False),
        sa.Column('client_request_id',sa.String(128),nullable=False),sa.Column('ordinal',sa.Integer(),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('account_id','client_request_id',name='uq_requested_paper_client'),
        sa.UniqueConstraint('account_id','ordinal',name='uq_requested_paper_ordinal'))
    op.create_table('requested_paper_events',
        sa.Column('request_id',sa.String(64),sa.ForeignKey('requested_paper_requests.request_id'),primary_key=True),
        sa.Column('sequence',sa.Integer(),primary_key=True),sa.Column('event_id',sa.String(128),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.Column('summary',sa.JSON(),nullable=False),sa.Column('summary_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('request_id','event_id',name='uq_requested_paper_event'))
    op.execute("CREATE FUNCTION requested_paper_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Requested Paper evidence is immutable'; END; $$")
    for table in ['requested_paper_requests','requested_paper_events']:
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')
    op.execute("""CREATE FUNCTION requested_paper_account_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF TG_OP='DELETE' OR NEW.account_id IS DISTINCT FROM OLD.account_id OR
       NEW.opening::jsonb IS DISTINCT FROM OLD.opening::jsonb OR NEW.opening_sha256 IS DISTINCT FROM OLD.opening_sha256 OR
       NEW.revision <> OLD.revision+1 THEN RAISE EXCEPTION 'Requested Paper account transition forbidden'; END IF;
    RETURN NEW; END; $$""")
    op.execute('CREATE TRIGGER requested_paper_account_guard BEFORE UPDATE OR DELETE ON requested_paper_accounts FOR EACH ROW EXECUTE FUNCTION requested_paper_account_guard()')


def downgrade():
    op.drop_table('requested_paper_events');op.drop_table('requested_paper_requests');op.drop_table('requested_paper_accounts')
    op.execute('DROP FUNCTION requested_paper_account_guard()');op.execute('DROP FUNCTION requested_paper_immutable()')
