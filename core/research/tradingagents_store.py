"""Dedicated research database, isolated from all account and order tables."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from core.paper.directional_store import Conflict, encoded


class ResearchStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        with self.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {'ta_jobs'}:
                raise ValueError('Dedicated research database required')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS ta_jobs(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, account TEXT NOT NULL, request TEXT NOT NULL,
                mode TEXT NOT NULL, state TEXT NOT NULL, snapshot TEXT NOT NULL, result TEXT,
                created TEXT NOT NULL, finished TEXT, UNIQUE(owner,request))''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    def _view(self, row):
        return {'job_id': row['id'], 'account_id': row['account'], 'mode': row['mode'], 'state': row['state'],
                'snapshot': json.loads(row['snapshot']), 'result': None if row['result'] is None else json.loads(row['result']),
                'created_at': row['created'], 'finished_at': row['finished'], 'trading_enabled': False}

    def prior(self, owner, request, account, mode):
        with self.connect() as db:
            row = db.execute('SELECT * FROM ta_jobs WHERE owner=? AND request=?', (owner, request)).fetchone()
            if row is None:
                return None
            if row['account'] != account or row['mode'] != mode:
                raise Conflict('Research request ID conflict')
            return self._view(row)

    def create(self, owner, account, request, mode, snapshot, now):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM ta_jobs WHERE owner=? AND request=?', (owner, request)).fetchone()
            if previous is not None:
                if previous['account'] != account or previous['mode'] != mode:
                    raise Conflict('Research request ID conflict')
                return self._view(previous)
            if db.execute('SELECT COUNT(*) FROM ta_jobs WHERE owner=?', (owner,)).fetchone()[0] >= 100:
                raise Conflict('Research history budget reached')
            if mode == 'ANALYZE':
                if db.execute("SELECT COUNT(*) FROM ta_jobs WHERE state IN ('QUEUED','RUNNING')").fetchone()[0] >= 2:
                    raise Conflict('Research capacity reached')
                if db.execute("SELECT COUNT(*) FROM ta_jobs WHERE owner=? AND state IN ('QUEUED','RUNNING')", (owner,)).fetchone()[0]:
                    raise Conflict('Research already active in this browser')
            job = str(uuid4())
            db.execute('INSERT INTO ta_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (job, owner, account, request, mode, 'QUEUED' if mode == 'ANALYZE' else 'SNAPSHOT_ONLY',
                        encoded(snapshot), None, now.isoformat(), None))
            db.commit()
            return self.read(owner, job)

    def read(self, owner, job):
        with self.connect() as db:
            row = db.execute('SELECT * FROM ta_jobs WHERE id=? AND owner=?', (job, owner)).fetchone()
            if row is None:
                raise KeyError(job)
            return self._view(row)

    def listing(self, owner):
        with self.connect() as db:
            return [self._view(r) for r in db.execute('SELECT * FROM ta_jobs WHERE owner=? ORDER BY created DESC,id DESC LIMIT 20', (owner,))]

    def recover(self, now):
        with self.connect() as db:
            db.execute("UPDATE ta_jobs SET state='INTERRUPTED', finished=? WHERE state='RUNNING'", (now.isoformat(),))

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM ta_jobs WHERE state='QUEUED' ORDER BY created,id LIMIT 1").fetchone()
            if row is None:
                db.commit()
                return None
            db.execute("UPDATE ta_jobs SET state='RUNNING' WHERE id=? AND state='QUEUED'", (row['id'],))
            db.commit()
            return row['owner'], self.read(row['owner'], row['id'])

    def finish(self, owner, job, state, result, now):
        with self.connect() as db:
            db.execute("UPDATE ta_jobs SET state=?,result=?,finished=? WHERE id=? AND owner=? AND state='RUNNING'",
                       (state, encoded(result), now.isoformat(), job, owner))
