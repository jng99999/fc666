"""Immutable local lifecycle events and explicit coverage."""
from alembic import op
import sqlalchemy as sa
revision='0013'
down_revision='0012'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('paper_lifecycle_coverage',
        sa.Column('preparation_id',sa.String(64),sa.ForeignKey('paper_preparations.preparation_id'),primary_key=True),
        sa.Column('version',sa.String(64),nullable=False),sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('origin',sa.String(32),nullable=False),
        sa.CheckConstraint("origin IN ('NEW_PREPARATION','LEGACY_PENDING_ENROLLMENT')",name='ck_lifecycle_origin'))
    op.create_table('paper_lifecycle_events',
        sa.Column('event_id',sa.String(64),primary_key=True),
        sa.Column('authorization_id',sa.String(64),sa.ForeignKey('paper_authorizations.authorization_id'),nullable=False),
        sa.Column('sequence',sa.Integer(),nullable=False),sa.Column('kind',sa.String(16),nullable=False),
        sa.Column('fill_id',sa.String(64),nullable=True),sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('authorization_id','sequence',name='uq_lifecycle_sequence'),
        sa.UniqueConstraint('authorization_id','fill_id',name='uq_lifecycle_fill'),
        sa.CheckConstraint('sequence >= 0 AND sequence <= 2',name='ck_lifecycle_sequence'),
        sa.CheckConstraint("kind IN ('CREATED','FILL','OUTCOME')",name='ck_lifecycle_kind'),
        sa.CheckConstraint("(kind='FILL' AND fill_id IS NOT NULL) OR (kind<>'FILL' AND fill_id IS NULL)",name='ck_lifecycle_fill'))
    op.execute("CREATE FUNCTION paper_lifecycle_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Paper lifecycle is immutable'; END; $$")
    for table in ['paper_lifecycle_coverage','paper_lifecycle_events']:
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION paper_lifecycle_immutable()')

def downgrade():
    op.drop_table('paper_lifecycle_events');op.drop_table('paper_lifecycle_coverage')
    op.execute('DROP FUNCTION paper_lifecycle_immutable()')
