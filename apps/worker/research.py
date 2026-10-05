"""Independent bounded research worker; PostgreSQL is the durable queue."""
import logging
import signal
from threading import Event
from uuid import uuid4
from sqlalchemy import create_engine
from apps.api.settings import Settings
from core.research.jobs import heartbeat,claim,execute

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO);stop=Event();owner=str(uuid4())
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    engine=create_engine(Settings().database_url.get_secret_value(),pool_pre_ping=True,hide_parameters=True,connect_args={'connect_timeout':2})
    try:
        while not stop.is_set():
            try:
                heartbeat(engine,owner);job_id=claim(engine,owner)
                if job_id:execute(engine,job_id,owner,stop.is_set)
            except Exception:logging.warning('research_worker_database_or_lease_unavailable')
            stop.wait(.5)
    finally:
        from sqlalchemy.orm import Session
        from core.storage.models import ResearchWorkerRecord
        try:
            with Session(engine) as session,session.begin():
                worker=session.get(ResearchWorkerRecord,owner)
                if worker:session.delete(worker)
        except Exception:logging.warning('research_worker_shutdown_heartbeat_cleanup_unavailable')
        engine.dispose()
