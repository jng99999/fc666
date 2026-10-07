"""Independent immutable completed Paper order intents."""
from alembic import op
import sqlalchemy as sa
revision='0010'
down_revision='0009'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('paper_streams',sa.Column('intent_version',sa.String(64),nullable=True))
    op.create_table('paper_order_intents',
        sa.Column('intent_id',sa.String(64),primary_key=True),
        sa.Column('session_id',sa.String(36),sa.ForeignKey('paper_streams.session_id'),nullable=False),
        sa.Column('order_id',sa.String(64),nullable=False),
        sa.Column('version',sa.String(64),nullable=False),
        sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('origin',sa.String(32),nullable=False),
        sa.Column('status',sa.String(24),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.Column('transitions',sa.JSON(),nullable=False),
        sa.UniqueConstraint('session_id','order_id',name='uq_paper_intent_order'),
        sa.CheckConstraint("status IN ('FILLED','PARTIAL_CANCELLED','REJECTED')",name='ck_paper_intent_status'),
        sa.CheckConstraint("origin IN ('BAR_ACCEPTANCE','HISTORICAL_MATERIALIZATION')",name='ck_paper_intent_origin'))
    op.create_index('paper_intents_account_idx','paper_order_intents',['session_id','order_id'])
    op.execute("CREATE FUNCTION paper_intent_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Paper order intents are immutable'; END; $$")
    op.execute('CREATE TRIGGER paper_intents_immutable BEFORE UPDATE OR DELETE ON paper_order_intents FOR EACH ROW EXECUTE FUNCTION paper_intent_immutable()')


def downgrade():
    op.drop_table('paper_order_intents')
    op.execute('DROP FUNCTION paper_intent_immutable()')
    op.drop_column('paper_streams','intent_version')
