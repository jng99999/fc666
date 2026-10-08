"""Opt-in health policies and immutable per-request health evidence."""
from importlib import import_module
from alembic import op
import sqlalchemy as sa
revision='0024'
down_revision='0023'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('requested_paper_accounts',sa.Column('health_version',sa.String(64),nullable=True))
    op.create_table('requested_paper_health_policies',
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_accounts.account_id'),primary_key=True),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False))
    op.create_table('requested_paper_health_gates',
        sa.Column('request_id',sa.String(64),sa.ForeignKey('requested_paper_requests.request_id'),primary_key=True),
        sa.Column('phase',sa.String(16),primary_key=True),
        sa.Column('account_id',sa.String(128),sa.ForeignKey('requested_paper_health_policies.account_id'),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.CheckConstraint("phase IN ('PREPARE','SUBMIT')",name='ck_requested_health_gate_phase'))
    for table in ['requested_paper_health_policies','requested_paper_health_gates']:
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')
    op.execute("""CREATE OR REPLACE FUNCTION requested_paper_account_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF TG_OP='DELETE' OR NEW.account_id IS DISTINCT FROM OLD.account_id OR
       NEW.opening::jsonb IS DISTINCT FROM OLD.opening::jsonb OR NEW.opening_sha256 IS DISTINCT FROM OLD.opening_sha256 THEN
       RAISE EXCEPTION 'Requested Paper account transition forbidden'; END IF;
    IF NEW.health_version IS DISTINCT FROM OLD.health_version THEN
       IF OLD.health_version IS NOT NULL OR NEW.health_version IS DISTINCT FROM 'paper-requested-health-policy-v1' OR
          OLD.revision <> 0 OR NEW.revision IS DISTINCT FROM OLD.revision OR
          NEW.current::jsonb IS DISTINCT FROM OLD.current::jsonb OR NEW.active_request_id IS DISTINCT FROM OLD.active_request_id OR
          NEW.control_version IS DISTINCT FROM OLD.control_version OR OLD.control_version IS NULL OR
          EXISTS (SELECT 1 FROM requested_paper_requests WHERE account_id=OLD.account_id) OR
          NOT EXISTS (SELECT 1 FROM requested_paper_health_policies WHERE account_id=OLD.account_id) THEN
          RAISE EXCEPTION 'Health enrollment forbidden'; END IF;
    ELSIF NEW.control_version IS DISTINCT FROM OLD.control_version THEN
       IF OLD.control_version IS NOT NULL OR NEW.control_version IS DISTINCT FROM 'paper-requested-controls-v1' OR
          NEW.revision IS DISTINCT FROM OLD.revision OR NEW.current::jsonb IS DISTINCT FROM OLD.current::jsonb OR
          NEW.active_request_id IS DISTINCT FROM OLD.active_request_id THEN RAISE EXCEPTION 'Control enrollment forbidden'; END IF;
    ELSE IF NEW.revision <> OLD.revision+1 THEN RAISE EXCEPTION 'Financial revision forbidden'; END IF; END IF;
    RETURN NEW; END; $$""")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM requested_paper_health_policies) OR EXISTS (SELECT 1 FROM requested_paper_health_gates) OR EXISTS (SELECT 1 FROM requested_paper_accounts WHERE health_version IS NOT NULL)")):
        raise RuntimeError('Cannot downgrade persisted health policy or gate evidence')
    import_module('migrations.versions.0016_requested_controls').guard(True)
    op.drop_table('requested_paper_health_gates');op.drop_table('requested_paper_health_policies')
    op.drop_column('requested_paper_accounts','health_version')
