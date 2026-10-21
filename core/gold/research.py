"""Owner-scoped persistent, bounded research queue. It never writes paper-account events."""
import json
import logging
import time
from uuid import uuid4

from core.gold.backtest import compare
from core.gold.history import canonical, sha
from core.gold.ledger import Conflict


class Research:
    def __init__(self, store, catalogue, clock=None):
        self.store, self.catalogue = store, catalogue
        self.clock = clock or (lambda: int(time.time()*1000))
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS gold_research_jobs(
                id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_id TEXT NOT NULL,request_hash TEXT NOT NULL,
                request TEXT NOT NULL,status TEXT NOT NULL,progress TEXT NOT NULL,error TEXT,
                summary TEXT,result TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
                UNIQUE(owner,request_id))''')

    def enqueue(self, owner, request_id, request):
        identity, now = str(uuid4()), self.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id,request_hash FROM gold_research_jobs WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
            if old:
                if old['request_hash'] != sha(request):
                    raise Conflict('Research request ID reused with different parameters')
                identity = old['id']
            else:
                if db.execute("SELECT count(*) FROM gold_research_jobs WHERE owner=? AND status IN ('QUEUED','RUNNING')", (owner,)).fetchone()[0] >= 2:
                    raise Conflict('最多同时保留两个未完成的黄金研究任务')
                if db.execute("SELECT count(*) FROM gold_research_jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[0] >= 8:
                    raise Conflict('黄金研究队列已满，请稍后重试')
                if db.execute('SELECT count(*) FROM gold_research_jobs').fetchone()[0] >= 2000:
                    raise Conflict('测试环境研究存储容量已满')
                db.execute('INSERT INTO gold_research_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (identity, owner, request_id, sha(request), canonical(request).decode(), 'QUEUED', '{"done":0,"total":null}',
                            None, None, None, now, now))
        return self.read(identity, owner)

    def read(self, identity, owner):
        with self.store.connect() as db:
            row = db.execute('SELECT id,request,status,progress,error,summary,created_at,updated_at FROM gold_research_jobs WHERE id=? AND owner=?', (identity, owner)).fetchone()
        if not row:
            raise KeyError(identity)
        result = dict(row)
        for key in ('request', 'progress', 'summary'):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        result['trading_enabled'] = False
        return result

    def list(self, owner):
        with self.store.connect() as db:
            rows = db.execute('SELECT id,status,created_at,updated_at,error FROM gold_research_jobs WHERE owner=? ORDER BY created_at DESC LIMIT 20', (owner,)).fetchall()
        return [dict(r) for r in rows]

    def result(self, identity, owner, member=None):
        with self.store.connect() as db:
            row = db.execute('SELECT result,status FROM gold_research_jobs WHERE id=? AND owner=?', (identity, owner)).fetchone()
        if not row:
            raise KeyError(identity)
        if row['status'] != 'SUCCEEDED':
            raise Conflict('研究尚未完成，不能导出结果')
        result = json.loads(row['result'])
        if member is None:
            return {'run_id': result['run_id'], 'manifest': result['manifest'], 'quality': result['quality'], 'trading_enabled': False}
        if not 0 <= member < len(result['results']):
            raise KeyError(member)
        return {**result['results'][member], 'run_id': result['run_id'], 'manifest': result['manifest']}

    def recover(self):
        with self.store.connect() as db:
            db.execute("UPDATE gold_research_jobs SET status='QUEUED',progress=?,updated_at=? WHERE status='RUNNING'", ('{"done":0,"total":null}', self.clock()))

    def execute_one(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT id,request FROM gold_research_jobs WHERE status='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return False
            identity, request = row['id'], json.loads(row['request'])
            db.execute("UPDATE gold_research_jobs SET status='RUNNING',error=NULL,updated_at=? WHERE id=?", (self.clock(), identity))
        try:
            data, bars = self.catalogue.load(request['dataset_id'])

            def progress(done, total):
                with self.store.connect() as db:
                    db.execute('UPDATE gold_research_jobs SET progress=?,updated_at=? WHERE id=?', (json.dumps({'done': done, 'total': total}), self.clock(), identity))

            result = compare(data, bars, request, progress)
            for item in result['results']:
                if len(canonical(item)) > 3000000:
                    raise ValueError('单项结果超过3MB，请缩短历史窗口后重试；结果没有被截断')
            raw = canonical(result)
            if len(raw) > 48000000:
                raise ValueError('完整研究结果超过48MB，请减少策略或窗口')
            summary = [{k: v for k, v in item.items() if k != 'fills' and
                        (k != 'equity' or (item['window'] == 'full' and item['scenario'] == 'configured_costs'))} | {'member': i}
                       for i, item in enumerate(result['results'])]
            with self.store.connect() as db:
                db.execute("UPDATE gold_research_jobs SET status='SUCCEEDED',summary=?,result=?,updated_at=? WHERE id=?",
                           (canonical(summary).decode(), raw.decode(), self.clock(), identity))
        except Exception as error:
            logging.getLogger('fc666.gold.research').warning('gold_research_failed: %s', type(error).__name__)
            message = str(error)[:300] if isinstance(error, (ValueError, KeyError, ArithmeticError)) else '黄金研究计算失败；未修改模拟账户，请重新提交研究任务'
            with self.store.connect() as db:
                db.execute("UPDATE gold_research_jobs SET status='FAILED',error=?,updated_at=? WHERE id=?", (message, self.clock(), identity))
        return True

    def work(self, stop):
        while not stop.is_set():
            if not self.execute_one():
                stop.wait(1)
