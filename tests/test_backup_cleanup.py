from uuid import uuid4
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.exc import OperationalError
from scripts.backup_drill import _drop_restore_database
from tests.test_integration import database


@pytest.mark.parametrize('target',['fc666','postgres','fc666_test_'+'0'*32,'fc666_restore_invalid','fc666_restore_'+'0'*32+'"'])
def test_cleanup_refuses_any_non_restore_target(target):
    class ForbiddenEngine:
        @property
        def url(self):raise AssertionError('Invalid target reached database access')
    with pytest.raises(ValueError):_drop_restore_database(ForbiddenEngine(),target)


def test_cleanup_terminates_active_target_connection_preserves_source(database):
    engine,_,_=database;target='fc666_restore_'+uuid4().hex
    admin=create_engine(engine.url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    clone=create_engine(engine.url.set(database=target));connection=None
    try:
        with admin.connect() as db:db.execute(text(f'CREATE DATABASE "{target}"'))
        connection=clone.connect();assert connection.scalar(text('SELECT current_database()'))==target
        _drop_restore_database(engine,target)
        with admin.connect() as db:assert db.scalar(text('SELECT count(*) FROM pg_database WHERE datname=:target'),{'target':target})==0
        with engine.connect() as db:assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0026'
        with pytest.raises(OperationalError):
            with create_engine(engine.url.set(database=target)).connect():pass
    finally:
        if connection is not None:connection.invalidate();connection.close()
        clone.dispose()
        with admin.connect() as db:
            if db.scalar(text('SELECT count(*) FROM pg_database WHERE datname=:target'),{'target':target}):_drop_restore_database(engine,target)
        admin.dispose()
