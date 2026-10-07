"""Durable prepared closed-bar batches before ledger acceptance."""
from alembic import op
import sqlalchemy as sa
revision='0011'
down_revision='0010'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('paper_preparations',
        sa.Column('preparation_id',sa.String(64),primary_key=True),
        sa.Column('session_id',sa.String(36),sa.ForeignKey('paper_streams.session_id'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('finished_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),sa.Column('reason',sa.String(32),nullable=True),
        sa.CheckConstraint("status IN ('PREPARED','CONSUMED','CANCELLED')",name='ck_preparation_status'),
        sa.CheckConstraint('finished_at IS NULL OR finished_at >= created_at',name='ck_preparation_clock'),
        sa.CheckConstraint("(status='PREPARED' AND finished_at IS NULL AND reason IS NULL) OR (status='CONSUMED' AND finished_at IS NOT NULL AND reason IS NULL) OR (status='CANCELLED' AND finished_at IS NOT NULL AND reason IS NOT NULL)",name='ck_preparation_outcome'))
    op.create_index('paper_preparation_pending_idx','paper_preparations',['session_id'],unique=True,postgresql_where=sa.text("status='PREPARED'"))
    op.create_index('paper_preparation_history_idx','paper_preparations',['session_id','created_at','preparation_id'])
    op.execute("""CREATE FUNCTION paper_preparation_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Paper preparation deletion forbidden'; END IF;
    IF OLD.status <> 'PREPARED' OR NEW.status NOT IN ('CONSUMED','CANCELLED') OR
       NEW.preparation_id IS DISTINCT FROM OLD.preparation_id OR NEW.session_id IS DISTINCT FROM OLD.session_id OR
       NEW.created_at IS DISTINCT FROM OLD.created_at OR NEW.payload::jsonb IS DISTINCT FROM OLD.payload::jsonb OR
       NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256 THEN RAISE EXCEPTION 'Paper preparation transition forbidden'; END IF;
    RETURN NEW; END; $$""")
    op.execute('CREATE TRIGGER paper_preparation_guard BEFORE UPDATE OR DELETE ON paper_preparations FOR EACH ROW EXECUTE FUNCTION paper_preparation_guard()')

def downgrade():
    op.drop_table('paper_preparations')
    op.execute('DROP FUNCTION paper_preparation_guard()')
