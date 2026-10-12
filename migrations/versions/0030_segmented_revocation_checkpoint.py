"""Anchor verified retained revocations without resetting existing denies."""
import hashlib,json,re
from alembic import op
import sqlalchemy as sa
revision='0030'
down_revision='0029'
branch_labels=None
depends_on=None


def verified_rows(bind):
    # Frozen v1 validation: migration must not import mutable application code.
    rows=list(bind.execute(sa.text('SELECT sequence,command_id,subject_sha256,ledger_id,action,payload,payload_sha256 FROM segmented_operator_revocations ORDER BY sequence LIMIT 1025')).mappings())
    if len(rows)>1024:raise ValueError('Revocation capacity exceeded during migration')
    parent=None;previous=0;commands=set();scopes=set()
    for index,row in enumerate(rows):
        value=row['payload']
        keys={'version','sequence','parent_sha256','command_id','subject_sha256','ledger_id','action','recorded_us','sha256'}
        if not isinstance(value,dict) or set(value)!=keys:raise ValueError('Invalid revocation migration evidence')
        digest=hashlib.sha256(json.dumps({key:item for key,item in value.items() if key!='sha256'},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if (value['version']!='paper-segmented-operator-revocation-v1' or type(value['sequence']) is not int
            or value['sequence']!=index or row['sequence']!=index or value['parent_sha256']!=parent
            or value['sha256']!=digest or row['payload_sha256']!=digest
            or any(row[key]!=value[key] for key in ['command_id','subject_sha256','ledger_id','action'])
            or not isinstance(value['subject_sha256'],str) or re.fullmatch('[0-9a-f]{64}',value['subject_sha256']) is None
            or not isinstance(value['command_id'],str) or not 1<=len(value['command_id'])<=128
            or value['action'] not in ('*','READ_SEGMENTED_EVIDENCE','ENROLL_SEGMENTED_WORKER')
            or type(value['recorded_us']) is not int or not 0<value['recorded_us']<=2**63-1 or value['recorded_us']<previous
            or not isinstance(value['ledger_id'],str) or (value['ledger_id']!='*' and (not value['ledger_id'].startswith('segmented:') or not 11<=len(value['ledger_id'])<=128))):
            raise ValueError('Invalid revocation migration lineage/identity')
        scope=(value['subject_sha256'],value['ledger_id'],value['action'])
        if value['command_id'] in commands or scope in scopes:raise ValueError('Duplicate revocation migration identity')
        commands.add(value['command_id']);scopes.add(scope);parent=digest;previous=value['recorded_us']
    return rows


def upgrade():
    bind=op.get_bind()
    bind.execute(sa.text("SELECT set_config('lock_timeout','2000ms',true)"))
    bind.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('fc666-segmented-operator-revocation-v1'))"))
    bind.execute(sa.text('LOCK TABLE segmented_operator_revocations IN SHARE ROW EXCLUSIVE MODE'))
    rows=verified_rows(bind)
    op.create_table('segmented_operator_revocation_head',
        sa.Column('identity',sa.String(16),primary_key=True),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('root_sha256',sa.String(64),nullable=True),
        sa.Column('head_sha256',sa.String(64),nullable=True),
        sa.CheckConstraint("identity='global'",name='ck_revocation_head_identity'),
        sa.CheckConstraint('revision>=0 AND revision<=1024',name='ck_revocation_head_revision'),
        sa.CheckConstraint("(revision=0 AND root_sha256 IS NULL AND head_sha256 IS NULL) OR (revision>0 AND root_sha256 ~ '^[0-9a-f]{64}$' AND head_sha256 ~ '^[0-9a-f]{64}$' AND root_sha256 IS NOT NULL AND head_sha256 IS NOT NULL)",name='ck_revocation_head_hashes'))
    bind.execute(sa.text("INSERT INTO segmented_operator_revocation_head(identity,revision,root_sha256,head_sha256) VALUES('global',:revision,:root,:head)"),
                 {'revision':len(rows),'root':rows[0]['payload_sha256'] if rows else None,'head':rows[-1]['payload_sha256'] if rows else None})
    op.execute("""CREATE FUNCTION segmented_revocation_head_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Revocation checkpoint deletion forbidden'; END IF;
        IF NEW.identity IS DISTINCT FROM OLD.identity OR NEW.revision <> OLD.revision+1 OR
           (OLD.revision>0 AND NEW.root_sha256 IS DISTINCT FROM OLD.root_sha256) OR
           NEW.head_sha256 IS NOT DISTINCT FROM OLD.head_sha256 THEN
           RAISE EXCEPTION 'Invalid revocation checkpoint transition'; END IF;
        RETURN NEW; END; $$""")
    op.execute('CREATE TRIGGER segmented_revocation_head_guard BEFORE UPDATE OR DELETE ON segmented_operator_revocation_head FOR EACH ROW EXECUTE FUNCTION segmented_revocation_head_guard()')


def downgrade():
    bind=op.get_bind()
    bind.execute(sa.text("SELECT set_config('lock_timeout','2000ms',true)"))
    bind.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('fc666-segmented-operator-revocation-v1'))"))
    current=bind.scalar(sa.text("SELECT revision FROM segmented_operator_revocation_head WHERE identity='global'"))
    if current!=0 or bind.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM segmented_operator_revocations)')):
        raise ValueError('Cannot discard populated or missing revocation checkpoint')
    op.drop_table('segmented_operator_revocation_head')
    op.execute('DROP FUNCTION segmented_revocation_head_guard()')
