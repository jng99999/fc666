"""Independent versioned local segmented ledgers, no historical account migration."""
from alembic import op
import sqlalchemy as sa
revision='0027'
down_revision='0026'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('segmented_paper_ledgers',
        sa.Column('ledger_id',sa.String(128),primary_key=True),
        sa.Column('root_sha256',sa.String(64),nullable=False),
        sa.Column('head_sha256',sa.String(64),nullable=False),
        sa.Column('commit_index',sa.Integer(),nullable=False),
        sa.Column('financial_revision',sa.Integer(),nullable=False),
        sa.Column('current',sa.JSON(),nullable=False),
        sa.CheckConstraint('commit_index>=0 AND commit_index<512',name='ck_segmented_ledger_index'),
        sa.CheckConstraint('financial_revision>=0',name='ck_segmented_ledger_revision'))
    op.create_table('segmented_paper_commits',
        sa.Column('ledger_id',sa.String(128),sa.ForeignKey('segmented_paper_ledgers.ledger_id'),primary_key=True),
        sa.Column('commit_index',sa.Integer(),primary_key=True),
        sa.Column('command_id',sa.String(128),nullable=True),
        sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.UniqueConstraint('ledger_id','command_id',name='uq_segmented_command'),
        sa.CheckConstraint('commit_index>=0 AND commit_index<512',name='ck_segmented_commit_index'),
        sa.CheckConstraint('octet_length(payload::text)<=35651584',name='ck_segmented_commit_bytes'))
    op.execute('CREATE TRIGGER segmented_paper_commits_immutable BEFORE UPDATE OR DELETE ON segmented_paper_commits FOR EACH ROW EXECUTE FUNCTION requested_paper_immutable()')
    op.execute("""CREATE FUNCTION segmented_paper_ledger_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Segmented ledger deletion forbidden'; END IF;
        IF NEW.ledger_id IS DISTINCT FROM OLD.ledger_id OR NEW.root_sha256 IS DISTINCT FROM OLD.root_sha256 OR
           NEW.commit_index <> OLD.commit_index+1 OR NEW.financial_revision <= OLD.financial_revision THEN
           RAISE EXCEPTION 'Invalid segmented financial head transition'; END IF;
        RETURN NEW; END; $$""")
    op.execute('CREATE TRIGGER segmented_paper_ledger_guard BEFORE UPDATE OR DELETE ON segmented_paper_ledgers FOR EACH ROW EXECUTE FUNCTION segmented_paper_ledger_guard()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM segmented_paper_ledgers)')):
        raise ValueError('Cannot discard populated segmented financial evidence')
    op.drop_table('segmented_paper_commits')
    op.drop_table('segmented_paper_ledgers')
    op.execute('DROP FUNCTION segmented_paper_ledger_guard()')
