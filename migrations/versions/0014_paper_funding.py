"""Account-scoped projected funding reservations and atomic settlement evidence."""
from alembic import op
import sqlalchemy as sa
from importlib import import_module
revision='0014'
down_revision='0013'
branch_labels=None
depends_on=None


def guard():
    op.execute('''CREATE OR REPLACE FUNCTION paper_preparation_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Paper preparation deletion forbidden'; END IF;
    IF OLD.status <> 'PREPARED' OR NEW.status NOT IN ('CONSUMED','CANCELLED') OR
       NEW.preparation_id IS DISTINCT FROM OLD.preparation_id OR NEW.session_id IS DISTINCT FROM OLD.session_id OR
       NEW.created_at IS DISTINCT FROM OLD.created_at OR NEW.payload::jsonb IS DISTINCT FROM OLD.payload::jsonb OR
       NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256 OR
       NEW.authorization_version IS DISTINCT FROM OLD.authorization_version OR
       NEW.funding_version IS DISTINCT FROM OLD.funding_version THEN RAISE EXCEPTION 'Paper preparation transition forbidden'; END IF;
    RETURN NEW; END; $$''')


def upgrade():
    op.add_column('paper_preparations',sa.Column('funding_version',sa.String(64),nullable=True))
    for table, parent in [('paper_funding_reservations','paper_preparations'),('paper_funding_outcomes','paper_funding_reservations')]:
        op.create_table(table,
            sa.Column('preparation_id',sa.String(64),sa.ForeignKey(f'{parent}.preparation_id'),primary_key=True),
            sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
            sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False))
    op.execute("CREATE FUNCTION paper_funding_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Paper funding evidence is immutable'; END; $$")
    for table in ['paper_funding_reservations','paper_funding_outcomes']:
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION paper_funding_immutable()')
    guard()


def downgrade():
    op.drop_table('paper_funding_outcomes');op.drop_table('paper_funding_reservations')
    op.execute('DROP FUNCTION paper_funding_immutable()')
    import_module('migrations.versions.0012_paper_authorizations').guard(True)
    op.drop_column('paper_preparations','funding_version')
