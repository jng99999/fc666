"""Durable isolated Paper fault laboratory; never reads/writes product accounts."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from core.paper.reconciliation import LIMIT, VERSION, reconcile


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class FaultAdapter:
    """Explicit dedicated file; serialized writes and immutable replay evidence.

    Batches may arrive reordered/duplicated but must complete a contiguous prefix.
    Gapped batches roll back; transport retries must resend the missing prefix.
    This is a local fault lab, not an exchange or shared-capital execution engine.
    """
    def __init__(self, path):
        self.path = Path(path)
        if self.path.suffix != '.sqlite3':
            raise ValueError('Dedicated .sqlite3 laboratory file required')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        with self.connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {'lab_requests','lab_events','lab_evidence','lab_leases','lab_dispatches'}:
                raise ValueError('Refusing unrelated database')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lab_requests (
                    order_id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lab_events (
                    order_id TEXT NOT NULL REFERENCES lab_requests(order_id),
                    event_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence BETWEEN 0 AND 999),
                    payload TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY(order_id,event_id), UNIQUE(order_id,sequence));
                CREATE TABLE IF NOT EXISTS lab_evidence (
                    order_id TEXT NOT NULL REFERENCES lab_requests(order_id),
                    event_count INTEGER NOT NULL CHECK(event_count BETWEEN 1 AND 1000),
                    payload TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY(order_id,event_count));
            ''')
            for table in ('lab_requests','lab_events','lab_evidence'):
                for action in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable laboratory evidence'); END")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def create(self, order_id, requested_quantity):
        if len(encoded({'order_id':order_id,'quantity':requested_quantity}).encode()) > 4096:
            raise ValueError('Oversized request')
        reconcile(order_id, requested_quantity, [])
        payload = {'version':VERSION,'order_id':order_id,'requested_quantity':requested_quantity,
                   'mode':'ISOLATED_PAPER_FAULT_LAB','execution_enabled':False}
        with self.transaction() as db:
            existing = db.execute('SELECT payload,digest FROM lab_requests WHERE order_id=?',(order_id,)).fetchone()
            if existing:
                if existing != (encoded(payload), sha(payload)):
                    raise ValueError('Conflicting or corrupt request')
            else:
                if db.execute('SELECT count(*) FROM lab_requests').fetchone()[0] >= LIMIT:
                    raise ValueError('Laboratory request capacity reached')
                db.execute('INSERT INTO lab_requests VALUES (?,?,?)',(order_id,encoded(payload),sha(payload)))
        return payload

    def _load(self, db, order_id):
        request = db.execute('SELECT payload,digest FROM lab_requests WHERE order_id=?',(order_id,)).fetchone()
        if request is None:
            raise ValueError('Unknown laboratory request')
        payload = json.loads(request[0])
        if sha(payload) != request[1] or payload != {
            'version':VERSION,'order_id':order_id,'requested_quantity':payload.get('requested_quantity'),
            'mode':'ISOLATED_PAPER_FAULT_LAB','execution_enabled':False}:
            raise ValueError('Corrupt laboratory request')
        rows = db.execute('SELECT event_id,sequence,payload,digest FROM lab_events WHERE order_id=? ORDER BY sequence',(order_id,)).fetchall()
        events = []
        for identity, sequence, raw, digest in rows:
            event = json.loads(raw)
            if sha(event) != digest or event.get('event_id') != identity or event.get('sequence') != sequence:
                raise ValueError('Corrupt laboratory event')
            events.append(event)
        return payload, events

    def _evidence(self, request, events, previous_count=0):
        return {'version':VERSION,'request_sha256':sha(request),'events_sha256':sha(events),
                'event_count':len(events), 'previous_event_count':previous_count, 'summary':reconcile(request['order_id'],request['requested_quantity'],events)}

    def _verify(self, db, request, events):
        # Every committed prefix has evidence; no history may silently disappear.
        evidence = db.execute('SELECT event_count,payload,digest FROM lab_evidence WHERE order_id=? ORDER BY event_count',(request['order_id'],)).fetchall()
        counts = [row[0] for row in evidence]
        if (events and (not counts or counts[-1] != len(events))) or (not events and counts):
            raise ValueError('Missing or unexpected reconciliation evidence')
        previous_count = 0
        for count, raw, digest in evidence:
            expected = self._evidence(request, events[:count], previous_count)
            previous_count = count
            if json.loads(raw) != expected or sha(expected) != digest:
                raise ValueError('Corrupt reconciliation evidence')
        return json.loads(evidence[-1][1]) if evidence else self._evidence(request, events)

    def append(self, order_id, batch):
        with self.transaction() as db:
            has_leases = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lab_leases'").fetchone()
            has_dispatches = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lab_dispatches'").fetchone()
            owned = has_leases and db.execute('SELECT 1 FROM lab_leases WHERE order_id=?',(order_id,)).fetchone()
            dispatched = has_dispatches and db.execute('SELECT 1 FROM lab_dispatches WHERE order_id=?',(order_id,)).fetchone()
            if owned or dispatched:
                raise ValueError('Owned laboratory request requires a current fencing token')
            return self._append(db, order_id, batch)

    def _append(self, db, order_id, batch):
        batch = deepcopy(batch)
        if not isinstance(batch, list) or not batch or len(batch) > LIMIT or len(encoded(batch).encode()) > 32*1024*1024:
            raise ValueError('Invalid bounded event batch')
        request, events = self._load(db, order_id)
        self._verify(db, request, events)
        by_id = {event['event_id']:event for event in events}
        for event in batch:
            if not isinstance(event, dict) or not isinstance(event.get('event_id'), str):
                raise ValueError('Invalid event identity')
            identity = event['event_id']
            if identity in by_id and by_id[identity] != event:
                raise ValueError('Conflicting duplicate event')
            by_id[identity] = event
        merged = list(by_id.values())
        if len(encoded(merged).encode()) > 32*1024*1024:
            raise ValueError('Oversized transcript')
        summary = reconcile(order_id, request['requested_quantity'], merged)
        existing_ids = {event['event_id'] for event in events}
        for event in merged:
            if event['event_id'] not in existing_ids:
                db.execute('INSERT INTO lab_events VALUES (?,?,?,?,?)',
                           (order_id,event['event_id'],event['sequence'],encoded(event),sha(event)))
        complete = sorted(by_id.values(), key=lambda event:event['sequence'])
        if len(complete) != len(events):
            evidence = self._evidence(request, complete, len(events))
            db.execute('INSERT INTO lab_evidence VALUES (?,?,?,?)',
                       (order_id,len(complete),encoded(evidence),sha(evidence)))
        return summary


    def inspect(self, order_id):
        with self.connection() as db:
            db.execute('BEGIN')
            request, events = self._load(db, order_id)
            evidence = self._verify(db, request, events)
            return {'request':request, 'events':events, 'evidence':evidence}
