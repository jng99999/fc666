"""Immutable Paper scenario definitions and valuation snapshots."""
from alembic import op
revision='0009'
down_revision='0008'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('\nCREATE TABLE portfolio_scenarios (\n\tscenario_id VARCHAR(36) NOT NULL, \n\trequest_id VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tdefinition JSON NOT NULL, \n\tdefinition_sha256 VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (scenario_id), \n\tUNIQUE (request_id)\n)\n\n')
    op.execute('CREATE INDEX portfolio_scenarios_created_idx ON portfolio_scenarios (created_at, scenario_id)')
    op.execute('\nCREATE TABLE portfolio_snapshots (\n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tscenario_id VARCHAR(36) NOT NULL, \n\trequest_id VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\treport JSON NOT NULL, \n\treport_sha256 VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (snapshot_id), \n\tCONSTRAINT uq_portfolio_snapshot_request UNIQUE (scenario_id, request_id), \n\tFOREIGN KEY(scenario_id) REFERENCES portfolio_scenarios (scenario_id)\n)\n\n')
    op.execute('CREATE INDEX portfolio_snapshots_created_idx ON portfolio_snapshots (scenario_id, created_at, snapshot_id)')
    op.execute("""CREATE FUNCTION portfolio_history_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Portfolio history is immutable'; END; $$""")
    op.execute('CREATE TRIGGER portfolio_scenarios_immutable BEFORE UPDATE OR DELETE ON portfolio_scenarios FOR EACH ROW EXECUTE FUNCTION portfolio_history_immutable()')
    op.execute('CREATE TRIGGER portfolio_snapshots_immutable BEFORE UPDATE OR DELETE ON portfolio_snapshots FOR EACH ROW EXECUTE FUNCTION portfolio_history_immutable()')


def downgrade():
    op.drop_table('portfolio_snapshots')
    op.drop_table('portfolio_scenarios')
    op.execute('DROP FUNCTION portfolio_history_immutable()')
