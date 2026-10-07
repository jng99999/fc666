"""Explicit local Paper policies, controls and immutable admission evidence."""
from alembic import op
import sqlalchemy as sa
revision='0016'
down_revision='0015'
branch_labels=None
depends_on=None


def guard(controlled):
    transition="""IF NEW.control_version IS DISTINCT FROM OLD.control_version THEN
    IF OLD.control_version IS NOT NULL OR NEW.control_version IS DISTINCT FROM 'paper-requested-controls-v1' OR
       NEW.revision IS DISTINCT FROM OLD.revision OR NEW.current::jsonb IS DISTINCT FROM OLD.current::jsonb OR
       NEW.active_request_id IS DISTINCT FROM OLD.active_request_id THEN RAISE EXCEPTION 'Control enrollment forbidden'; END IF;
    ELSE IF NEW.revision <> OLD.revision+1 THEN RAISE EXCEPTION 'Financial revision forbidden'; END IF; END IF;""" if controlled else "IF NEW.revision <> OLD.revision+1 THEN RAISE EXCEPTION 'Financial revision forbidden'; END IF;"
    op.execute("""CREATE OR REPLACE FUNCTION requested_paper_account_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF TG_OP='DELETE' OR NEW.account_id IS DISTINCT FROM OLD.account_id OR
       NEW.opening::jsonb IS DISTINCT FROM OLD.opening::jsonb OR NEW.opening_sha256 IS DISTINCT FROM OLD.opening_sha256 THEN
       RAISE EXCEPTION 'Requested Paper account transition forbidden'; END IF;
    """+transition+" RETURN NEW; END; $$")


def upgrade():
    op.add_column('requested_paper_accounts',sa.Column('control_version',sa.String(64),nullable=True))
    op.create_table('requested_paper_policies',
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),primary_key=True),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False))
    op.create_table('requested_paper_controls',
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_policies.account_id'),primary_key=True),
        sa.Column('sequence',sa.Integer(),primary_key=True),sa.Column('command_id',sa.String(128),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('account_id','command_id',name='uq_requested_paper_command'))
    op.create_table('requested_paper_gates',
        sa.Column('request_id',sa.String(64),sa.ForeignKey('requested_paper_requests.request_id'),primary_key=True),
        sa.Column('phase',sa.String(16),primary_key=True),sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.CheckConstraint("phase IN ('PREPARE','SUBMIT')",name='ck_requested_paper_gate_phase'))
    for table in ['requested_paper_policies','requested_paper_controls','requested_paper_gates']:
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')
    guard(True)


def downgrade():
    op.drop_table('requested_paper_gates');op.drop_table('requested_paper_controls');op.drop_table('requested_paper_policies')
    guard(False);op.drop_column('requested_paper_accounts','control_version')
