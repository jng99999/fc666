"""Real-time paper consumes only durable finalized public bars; never sends orders."""
import logging
import signal
from threading import Event
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from apps.api.settings import Settings
from core.paper import streams
from core.storage.models import PaperWorkerRecord

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO);stop=Event();owner=str(uuid4())
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    engine=create_engine(Settings().database_url.get_secret_value(),pool_pre_ping=True,hide_parameters=True,connect_args={'connect_timeout':2})
    try:
        while not stop.is_set():
            try:
                streams.heartbeat(engine,owner)
                for session_id in streams.active(engine):
                    if stop.is_set():break
                    try:streams.advance(engine,session_id)
                    except Exception:logging.warning('paper_account_advance_failed_closed')
            except Exception:logging.warning('paper_worker_database_unavailable')
            stop.wait(.5)
    finally:
        try:
            with Session(engine) as session,session.begin():
                record=session.get(PaperWorkerRecord,owner)
                if record:session.delete(record)
        except Exception:logging.warning('paper_worker_heartbeat_cleanup_unavailable')
        engine.dispose()
