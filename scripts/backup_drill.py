"""Verify a local PG custom archive by restoring a new isolated database; no main writes."""
import argparse,json,os,subprocess,hashlib,uuid,re
from pathlib import Path
from sqlalchemy import create_engine,text
from apps.api.settings import Settings

ROOT=Path(__file__).resolve().parents[1]
DOCKER=['env','-u','DOCKER_HOST','-u','DOCKER_CONTEXT','-u','DOCKER_TLS','-u','DOCKER_TLS_VERIFY','-u','DOCKER_CERT_PATH','docker','--host=unix:///var/run/docker.sock']
COMPOSE=[*DOCKER,'compose','--env-file',str(ROOT/'.env'),'-f',str(ROOT/'infrastructure/compose.yaml')]


def run(args,**kwargs):
    result=subprocess.run(args,stderr=subprocess.PIPE,**kwargs)
    if result.returncode:raise RuntimeError('Backup/restore operation failed; sensitive diagnostics suppressed')
    return result


def catalog(connection):
    names=connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).all()
    connection.execute(text("SET TIME ZONE 'UTC'"))
    counts={};digests={}
    for name in names:
        table='public.'+connection.dialect.identifier_preparer.quote(name)
        digest=hashlib.sha256();count=0
        query=text('SELECT to_jsonb(t)::text AS row FROM '+table+' t ORDER BY (to_jsonb(t)::text) COLLATE "C"')
        for value in connection.execution_options(stream_results=True).scalars(query):
            digest.update(value.encode());digest.update(b'\n');count+=1
        counts[name]=count;digests[name]=digest.hexdigest()
    return {'schema':connection.scalar(text('SELECT version_num FROM alembic_version')),'tables':counts,'table_sha256':digests}


def drill(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if directory.is_symlink():raise ValueError('Backup directory must not be a symlink')
    os.chmod(directory,0o700)
    settings=Settings();engine=create_engine(settings.database_url.get_secret_value(),hide_parameters=True)
    # Supported local compose only; do not silently back up another database/server.
    if engine.url.host not in ['127.0.0.1','localhost'] or re.fullmatch(r'fc666(?:_test_[0-9a-f]{32})?',engine.url.database or '') is None or engine.url.username!='fc666' or engine.url.port!=5432:raise ValueError('Local compose database required')
    target='fc666_restore_'+uuid.uuid4().hex;archive=directory/(target+'.dump')
    manifest=directory/(target+'.json');created=False
    exec_prefix=[*COMPOSE,'exec','-T','postgres']
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as db,db.begin():
            snapshot=db.scalar(text('SELECT pg_export_snapshot()'));expected=catalog(db)
            fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as file:
                run([*exec_prefix,'pg_dump','-U','fc666','-d',engine.url.database,'-Fc','--no-owner','--no-acl','--snapshot',snapshot],stdout=file)
                file.flush();os.fsync(file.fileno())
        run([*exec_prefix,'createdb','-U','fc666','--template=template0',target],stdout=subprocess.DEVNULL);created=True
        run([*exec_prefix,'psql','-U','fc666','-d',target,'-v','ON_ERROR_STOP=1','-c','CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();'],stdout=subprocess.DEVNULL)
        with archive.open('rb') as file:
            run([*exec_prefix,'pg_restore','-U','fc666','-d',target,'--exit-on-error','--no-owner','--no-acl'],stdin=file,stdout=subprocess.DEVNULL)
        run([*exec_prefix,'psql','-U','fc666','-d',target,'-v','ON_ERROR_STOP=1','-c','SELECT timescaledb_post_restore();'],stdout=subprocess.DEVNULL)
        restored=create_engine(engine.url.set(database=target),hide_parameters=True)
        try:
            with restored.connect() as db:actual=catalog(db)
            if actual!=expected:raise RuntimeError('Restored schema/table counts differ from snapshot')
        finally:restored.dispose()
        digest=hashlib.sha256()
        with archive.open('rb') as file:
            for chunk in iter(lambda:file.read(1024*1024),b''):digest.update(chunk)
        report={'version':'fc666-local-backup-drill-v1','archive':archive.name,'archive_sha256':digest.hexdigest(),
                'bytes':archive.stat().st_size,'restored_schema':actual['schema'],'verified_table_counts':actual['tables'],'verified_table_sha256':actual['table_sha256'],
                'verification':'snapshot-schema-counts-and-row-content-sha256','row_content_verified':True,'offsite_retention_verified':False,
                'isolated_restore_verified':True,'main_database_modified':False}
        fd=os.open(manifest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as file:json.dump(report,file,sort_keys=True);file.flush();os.fsync(file.fileno())
        return report
    finally:
        engine.dispose()
        if created:run([*exec_prefix,'dropdb','-U','fc666','--force',target],stdout=subprocess.DEVNULL)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,default=ROOT/'.runtime/backups');args=parser.parse_args()
    report=drill(args.directory);print(json.dumps({k:v for k,v in report.items() if k not in ['verified_table_counts','verified_table_sha256']},sort_keys=True))


if __name__=='__main__':main()
