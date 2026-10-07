"""Immutable projected-order authorization committed before consumption."""
from alembic import op
import sqlalchemy as sa
revision='0012'
down_revision='0011'
branch_labels=None
depends_on=None

def guard(with_version):
    clause='OR NEW.authorization_version IS DISTINCT FROM OLD.authorization_version' if with_version else ''
    op.execute(f"""CREATE OR REPLACE FUNCTION paper_preparation_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Paper preparation deletion forbidden'; END IF;
    IF OLD.status <> 'PREPARED' OR NEW.status NOT IN ('CONSUMED','CANCELLED') OR
       NEW.preparation_id IS DISTINCT FROM OLD.preparation_id OR NEW.session_id IS DISTINCT FROM OLD.session_id OR
       NEW.created_at IS DISTINCT FROM OLD.created_at OR NEW.payload::jsonb IS DISTINCT FROM OLD.payload::jsonb OR
       NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256 {clause} THEN RAISE EXCEPTION 'Paper preparation transition forbidden'; END IF;
    RETURN NEW; END; $$""")

def upgrade():
    op.add_column('paper_preparations',sa.Column('authorization_version',sa.String(64),nullable=True))
    op.create_table('paper_authorizations',
        sa.Column('authorization_id',sa.String(64),primary_key=True),
        sa.Column('preparation_id',sa.String(64),sa.ForeignKey('paper_preparations.preparation_id'),nullable=False),
        sa.Column('order_id',sa.String(64),nullable=False),
        sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('preparation_id','order_id',name='uq_paper_authorization_order'))
    op.execute("CREATE FUNCTION paper_authorization_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Paper authorization is immutable'; END; $$")
    op.execute('CREATE TRIGGER paper_authorization_immutable BEFORE UPDATE OR DELETE ON paper_authorizations FOR EACH ROW EXECUTE FUNCTION paper_authorization_immutable()')
    guard(True)

def downgrade():
    op.drop_table('paper_authorizations')
    op.execute('DROP FUNCTION paper_authorization_immutable()')
    guard(False)
    op.drop_column('paper_preparations','authorization_version')
