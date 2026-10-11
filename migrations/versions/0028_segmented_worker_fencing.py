"""Immutable trusted local worker enrollment, leases and financial admissions."""
from alembic import op
import sqlalchemy as sa
revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('segmented_paper_ledgers', sa.Column('worker_version', sa.String(64), nullable=True))
    op.create_check_constraint('ck_segmented_worker_version', 'segmented_paper_ledgers', "worker_version IS NULL OR worker_version='paper-segmented-local-worker-v1'")
    op.execute("""CREATE OR REPLACE FUNCTION segmented_paper_ledger_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Segmented ledger deletion forbidden'; END IF;
        IF NEW.ledger_id IS DISTINCT FROM OLD.ledger_id OR NEW.root_sha256 IS DISTINCT FROM OLD.root_sha256 THEN
            RAISE EXCEPTION 'Invalid segmented root transition'; END IF;
        IF OLD.worker_version IS NULL AND NEW.worker_version='paper-segmented-local-worker-v1' AND
           OLD.commit_index=0 AND NEW.commit_index=0 AND NEW.head_sha256=OLD.head_sha256 AND
           NEW.financial_revision=OLD.financial_revision AND NEW.current::text=OLD.current::text THEN RETURN NEW; END IF;
        IF NEW.worker_version IS DISTINCT FROM OLD.worker_version OR
           NEW.commit_index <> OLD.commit_index+1 OR NEW.financial_revision <= OLD.financial_revision THEN
            RAISE EXCEPTION 'Invalid segmented financial head transition'; END IF;
        RETURN NEW; END; $$""")
    op.create_table('segmented_paper_worker_records',
        sa.Column('ledger_id', sa.String(128), sa.ForeignKey('segmented_paper_ledgers.ledger_id'), primary_key=True),
        sa.Column('sequence', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('command_id', sa.String(128), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('payload_sha256', sa.String(64), nullable=False),
        sa.UniqueConstraint('ledger_id', 'kind', 'command_id', name='uq_segmented_worker_command'),
        sa.CheckConstraint('sequence>=0 AND sequence<576', name='ck_segmented_worker_sequence'),
        sa.CheckConstraint("kind IN ('ENROLL','CLAIM','ACCEPT')", name='ck_segmented_worker_kind'),
        sa.CheckConstraint('octet_length(payload::text)<=16384', name='ck_segmented_worker_bytes'))
    op.execute('CREATE TRIGGER segmented_paper_worker_immutable BEFORE UPDATE OR DELETE ON segmented_paper_worker_records FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM segmented_paper_worker_records) OR EXISTS(SELECT 1 FROM segmented_paper_ledgers WHERE worker_version IS NOT NULL)')):
        raise ValueError('Cannot discard populated worker fencing evidence')
    op.drop_table('segmented_paper_worker_records')
    op.execute("""CREATE OR REPLACE FUNCTION segmented_paper_ledger_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Segmented ledger deletion forbidden'; END IF;
        IF NEW.ledger_id IS DISTINCT FROM OLD.ledger_id OR NEW.root_sha256 IS DISTINCT FROM OLD.root_sha256 OR
           NEW.commit_index <> OLD.commit_index+1 OR NEW.financial_revision <= OLD.financial_revision THEN
           RAISE EXCEPTION 'Invalid segmented financial head transition'; END IF;
        RETURN NEW; END; $$""")
    op.drop_constraint('ck_segmented_worker_version', 'segmented_paper_ledgers', type_='check')
    op.drop_column('segmented_paper_ledgers', 'worker_version')
