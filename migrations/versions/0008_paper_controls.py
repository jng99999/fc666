"""Append-only transactional Paper control history."""
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE paper_controls (
        event_id VARCHAR(36) NOT NULL,
        recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
        kind VARCHAR(8) NOT NULL,
        session_id VARCHAR(36) NOT NULL,
        action VARCHAR(8) NOT NULL,
        count INTEGER NOT NULL,
        expected_revision INTEGER NOT NULL,
        before_revision INTEGER NOT NULL,
        after_revision INTEGER NOT NULL,
        outcome VARCHAR(8) NOT NULL,
        before JSON NOT NULL,
        after JSON NOT NULL,
        PRIMARY KEY (event_id),
        CONSTRAINT ck_control_kind CHECK (kind IN ('sessions','streams')),
        CONSTRAINT ck_control_outcome CHECK (outcome IN ('APPLIED','NOOP','CONFLICT')),
        CONSTRAINT ck_control_values CHECK (expected_revision >= 0 AND before_revision >= 0 AND after_revision >= 0 AND count BETWEEN 1 AND 10),
        CONSTRAINT ck_control_revision CHECK ((outcome = 'APPLIED' AND expected_revision = before_revision AND after_revision = before_revision + 1) OR (outcome = 'NOOP' AND expected_revision = before_revision AND after_revision = before_revision) OR (outcome = 'CONFLICT' AND expected_revision <> before_revision AND after_revision = before_revision))
        )
    """)
    op.execute('CREATE INDEX paper_controls_account_idx ON paper_controls (kind, session_id, recorded_at, event_id)')
    op.execute("""CREATE FUNCTION paper_controls_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Paper controls are append-only'; END; $$""")
    op.execute("""CREATE TRIGGER paper_controls_immutable BEFORE UPDATE OR DELETE
        ON paper_controls FOR EACH ROW EXECUTE FUNCTION paper_controls_immutable()""")


def downgrade():
    op.drop_table('paper_controls')
    op.execute('DROP FUNCTION paper_controls_immutable()')
