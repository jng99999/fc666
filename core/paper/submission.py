"""Fenced local simulation ownership and query-only unknown-result recovery."""
import time
import json
from core.paper.fault_adapter import FaultAdapter, encoded, sha

VERSION = 'paper-fenced-submission-v1'


def clock():
    return time.time_ns()


class SubmissionAdapter(FaultAdapter):
    def __init__(self, path):
        super().__init__(path)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lab_leases (
                    order_id TEXT PRIMARY KEY REFERENCES lab_requests(order_id),
                    owner TEXT NOT NULL, token INTEGER NOT NULL CHECK(token>0),
                    expires_ns INTEGER NOT NULL CHECK(expires_ns>0));
                CREATE TABLE IF NOT EXISTS lab_dispatches (
                    order_id TEXT PRIMARY KEY REFERENCES lab_requests(order_id),
                    payload TEXT NOT NULL, digest TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS lab_dispatches_update BEFORE UPDATE ON lab_dispatches
                    BEGIN SELECT RAISE(ABORT,'Immutable dispatch'); END;
                CREATE TRIGGER IF NOT EXISTS lab_dispatches_delete BEFORE DELETE ON lab_dispatches
                    BEGIN SELECT RAISE(ABORT,'Immutable dispatch'); END;
                CREATE TRIGGER IF NOT EXISTS lab_leases_update BEFORE UPDATE ON lab_leases
                    WHEN NEW.order_id<>OLD.order_id OR NEW.token<>OLD.token+1 OR NEW.expires_ns<=OLD.expires_ns
                    BEGIN SELECT RAISE(ABORT,'Invalid fencing transition'); END;
                CREATE TRIGGER IF NOT EXISTS lab_leases_delete BEFORE DELETE ON lab_leases
                    BEGIN SELECT RAISE(ABORT,'Ownership cannot be removed'); END;
            ''')

    def claim(self, order_id, owner, ttl_seconds=30):
        if not isinstance(owner,str) or not owner or len(owner)>128 or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 60:
            raise ValueError('Invalid lease owner or duration')
        with self.transaction() as db:
            request, events = self._load(db,order_id)
            self._verify(db,request,events)
            row = db.execute('SELECT owner,token,expires_ns FROM lab_leases WHERE order_id=?',(order_id,)).fetchone()
            if row:
                self._dispatch(db,request,events)
            now = clock()
            if row and row[2]>now:
                if row[0] != owner:
                    raise ValueError('Request already owned')
                return {'owner':owner,'token':row[1],'expires_ns':row[2]}
            if row is None and events:
                raise ValueError('Legacy transcript cannot acquire retrospective ownership')
            token = row[1]+1 if row else 1
            expires = now + ttl_seconds*1_000_000_000
            db.execute('INSERT INTO lab_leases VALUES (?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET owner=excluded.owner,token=excluded.token,expires_ns=excluded.expires_ns',
                       (order_id,owner,token,expires))
            return {'owner':owner,'token':token,'expires_ns':expires}

    def _owned(self, db, order_id, owner, token):
        row = db.execute('SELECT owner,token,expires_ns FROM lab_leases WHERE order_id=?',(order_id,)).fetchone()
        if type(token) is not int or not row or row[0]!=owner or row[1]!=token or row[2]<=clock():
            raise ValueError('Expired or fenced owner')
        return row

    def _dispatch(self, db, request, events):
        row = db.execute('SELECT payload,digest FROM lab_dispatches WHERE order_id=?',(request['order_id'],)).fetchone()
        if row is None:
            if events:
                raise ValueError('Missing dispatch evidence')
            return None
        payload = json.loads(row[0])
        expected_keys = {'version','order_id','client_order_id','request_sha256','token','recorded_ns','event_sha256'}
        if (set(payload)!=expected_keys or sha(payload)!=row[1] or payload['version']!=VERSION
            or payload['order_id']!=request['order_id'] or payload['client_order_id']!=sha({'version':VERSION,'order_id':request['order_id']})
            or payload['request_sha256']!=sha(request) or type(payload['token']) is not int or payload['token']<1
            or type(payload['recorded_ns']) is not int or payload['recorded_ns']<1
            or len(events)<2 or events[0]!=self._initial(request['order_id'])[0]
            or events[1]!=self._initial(request['order_id'])[1] or payload['event_sha256']!=sha(events[:2])):
            raise ValueError('Corrupt dispatch evidence')
        lease = db.execute('SELECT token FROM lab_leases WHERE order_id=?',(request['order_id'],)).fetchone()
        if not lease or payload['token'] > lease[0]:
            raise ValueError('Dispatch has no valid ownership history')
        return payload

    def _initial(self, order_id):
        return [{'event_id':sha({'version':VERSION,'order_id':order_id,'sequence':seq}),
                 'order_id':order_id,'sequence':seq,'kind':kind,'payload':{}}
                for seq,kind in enumerate(('SUBMIT','UNKNOWN_SUBMISSION'))]

    def submit(self, order_id, owner, token):
        with self.transaction() as db:
            self._owned(db,order_id,owner,token)
            request, events = self._load(db,order_id)
            self._verify(db,request,events)
            dispatch = self._dispatch(db,request,events)
            if dispatch is not None:
                return dispatch  # Lost reply/takeover returns the original attempt.
            initial = self._initial(order_id)
            payload = {'version':VERSION,'order_id':order_id,'client_order_id':sha({'version':VERSION,'order_id':order_id}),
                       'request_sha256':sha(request),'token':token,'recorded_ns':clock(),'event_sha256':sha(initial)}
            self._append(db,order_id,initial)
            db.execute('INSERT INTO lab_dispatches VALUES (?,?,?)',(order_id,encoded(payload),sha(payload)))
            self._owned(db,order_id,owner,token)  # Expiration during verification rolls back.
            return payload

    def deliver(self, order_id, owner, token, batch):
        with self.transaction() as db:
            self._owned(db,order_id,owner,token)
            request, events = self._load(db,order_id)
            self._verify(db,request,events)
            if self._dispatch(db,request,events) is None:
                raise ValueError('Submission must be durable before delivery')
            if not isinstance(batch,list) or any(not isinstance(event,dict) or event.get('kind')=='SUBMIT' for event in batch):
                raise ValueError('Delivery cannot create a new submission')
            summary = self._append(db,order_id,batch)
            self._owned(db,order_id,owner,token)
            return summary

    def recover(self, order_id, owner, token):
        with self.transaction() as db:
            self._owned(db,order_id,owner,token)
            request, events = self._load(db,order_id)
            evidence = self._verify(db,request,events)
            dispatch = self._dispatch(db,request,events)
            state = evidence['summary']['state']
            action = ('NOT_STARTED' if dispatch is None else 'WAIT_UNKNOWN_RESULT' if state=='UNKNOWN_SUBMISSION'
                      else 'RECONCILE_EXISTING_SUBMISSION')
            self._owned(db,order_id,owner,token)
            return {'version':VERSION,'dispatch':dispatch,'evidence':evidence,'action':action,
                    'resubmission_allowed':False,'execution_enabled':False,'external_query_supported':False}

    def inspect(self, order_id):
        with self.connection() as db:
            db.execute('BEGIN')
            request, events = self._load(db,order_id)
            evidence = self._verify(db,request,events)
            dispatch = self._dispatch(db,request,events)
            return {'request':request,'events':events,'evidence':evidence,'dispatch':dispatch}
