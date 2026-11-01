"""Explicit, owner-scoped AI PAPER controls. No broker or live execution path."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
import sqlite3
from uuid import uuid4

from core.backtest.spot import digest
from core.paper.directional import Config, Proposal
from core.paper.directional_store import Conflict
from core.research.tradingagents import assess, freeze


class Automation:
    def __init__(self, service):
        self.service = service
        self.path = service.jobs.path.with_name('ai-paper.controls.sqlite3')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS controls(account TEXT PRIMARY KEY,owner TEXT NOT NULL,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS commands(owner TEXT NOT NULL,request TEXT NOT NULL,signature TEXT NOT NULL,
                    result TEXT NOT NULL,PRIMARY KEY(owner,request));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    def initial(self, account):
        return dict(account_id=account, enabled=False, epoch=0, interval_seconds=300,
                    next_due=None, last_job=None, state='PAUSED', receipt=None, trading_enabled=False)

    def read(self, db, owner, account):
        row = db.execute('SELECT owner,value FROM controls WHERE account=?', (account,)).fetchone()
        if row is None:
            return self.initial(account)
        if row['owner'] != owner:
            raise KeyError(account)
        return json.loads(row['value'])

    def save(self, db, owner, value):
        db.execute('INSERT INTO controls VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET value=excluded.value',
                   (value['account_id'], owner, json.dumps(value, allow_nan=False)))

    def gate(self, value, account):
        if not value['enabled']:
            return 'PAUSED'
        if account['status'] != 'RUNNING' or account['entry_halted']:
            return 'ACCOUNT_STOPPED'
        if account['metadata'].get('ai_paused', True):
            return 'PAUSED'
        status = self.service.status()
        if not status['model_configured']:
            return status['state']
        if account['account_id'] not in self.service.runner.accounts:
            return 'ACCOUNT_NOT_AUTHORIZED'
        now = self.service.clock()
        frozen = freeze(account, now=now, news=self.service.news(account['config']['instrument'], now))
        # Even with missing news, an existing position keeps its independent TP/SL.
        if frozen['snapshot']['entry_gate'] != 'CLEAR' and account['position'] is None:
            return frozen['snapshot']['entry_gate']
        if account['pending'] is not None:
            return 'ACCOUNT_BUSY'
        return 'READY'

    def view(self, owner, account_id):
        account = self.service.accounts.read(owner, account_id)
        with self.connect() as db:
            value = self.read(db, owner, account_id)
        gate = self.gate(value, account)
        if gate != 'READY':
            value['state'] = gate
        receipt = value['receipt']
        if receipt is None and value['last_job']:
            original = self.service.accounts.decision_receipt(owner, account_id, value['last_job'])
            if original is not None:
                receipt = dict(job_id=value['last_job'], request_id=value['last_job'],
                    at=original['request']['available_at'], **original['result'],
                    account_revision=account['revision'], fills_count=account['fills_count'])
                value['receipt'] = receipt
        if receipt:
            matching = [fill for fill in account['fills'] if fill.get('request_id') == receipt['request_id']]
            if matching:
                value['receipt'] = {**receipt, 'status': 'FILLED', 'reason': matching[-1]['reason'],
                                    'account_revision': account['revision'], 'fills_count': account['fills_count']}
            terminal = self.service.accounts.decision_outcome(owner, account_id, receipt['request_id'])
            if terminal:
                value['receipt'] = {**receipt, **terminal, 'account_revision': account['revision'], 'fills_count': account['fills_count']}
        outcome = account.get('last_outcome')
        if receipt and outcome and receipt.get('request_id') == outcome.get('request_id'):
            value['receipt'] = {**receipt, 'status': outcome['status'], 'reason': outcome['reason'],
                                'fills_count': account['fills_count'], 'account_revision': account['revision']}
        return value

    def control(self, owner, account_id, body):
        account = self.service.accounts.read(owner, account_id)
        signature = digest({'account_id': account_id, **body})
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT signature,result FROM commands WHERE owner=? AND request=?',
                             (owner, str(body['request_id']))).fetchone()
            if old:
                if old['signature'] != signature:
                    raise Conflict('AI control request ID conflict')
                return json.loads(old['result'])
            value = self.read(db, owner, account_id)
            if body['expected_epoch'] != value['epoch']:
                raise Conflict('AI control changed; refresh before confirming')
            if account['metadata']['strategy'] != 'AI_RESEARCH':
                raise Conflict('Create a dedicated AI_RESEARCH account; existing accounts cannot be taken over')
            if body['action'] not in ('START', 'PAUSE') or not 300 <= body['interval_seconds'] <= 86400:
                raise ValueError('Bounded explicit AI control required')
            if db.execute('SELECT COUNT(*) FROM commands WHERE owner=?', (owner,)).fetchone()[0] >= 10000:
                raise Conflict('AI control audit budget reached')
            account = self.service.accounts.pause_ai(owner, account_id, str(body['request_id']),
                body['expected_revision'], self.service.clock(), body['action'] == 'PAUSE')
            value.update(enabled=body['action'] == 'START', epoch=value['epoch']+1,
                         interval_seconds=body['interval_seconds'], next_due=self.service.clock().isoformat(),
                         last_job=None, receipt=None, state='PAUSED')
            value['state'] = self.gate(value, account)
            self.save(db, owner, value)
            db.execute('INSERT INTO commands VALUES(?,?,?,?)',
                       (owner, str(body['request_id']), signature, json.dumps(value)))
            db.commit()
            return value

    def schedule(self):
        with self.connect() as db:
            rows = db.execute('SELECT owner,value FROM controls ORDER BY account').fetchall()
        for row in rows:
            owner = row['owner']
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                value = self.read(db, owner, json.loads(row['value'])['account_id'])
                if not value['enabled']:
                    continue
                account = self.service.accounts.read(owner, value['account_id'])
                value['state'] = self.gate(value, account)
                now = self.service.clock()
                if value['state'] == 'READY':
                    previous = None
                    if value['last_job']:
                        try:
                            previous = self.service.storage().read(owner, value['last_job'])
                        except KeyError:
                            pass
                    if previous and previous['state'] in ('QUEUED', 'RUNNING'):
                        value['state'] = 'RESEARCH_QUEUED'
                    elif value['next_due'] and now < datetime.fromisoformat(value['next_due']):
                        value['state'] = 'WAIT_INTERVAL'
                    else:
                        snapshot = self.service.freeze(account, now, self.service.news(account['config']['instrument'], now))
                        if snapshot['snapshot'].get('market_evidence', {}).get('status', 'READY') != 'READY' and account['position'] is None:
                            value['state'] = 'HISTORY_UNAVAILABLE'
                            self.save(db, owner, value)
                            db.commit()
                            continue
                        snapshot['automation'] = {'epoch': value['epoch']}
                        try:
                            job = self.service.storage().create(owner, value['account_id'], str(uuid4()), 'AUTO', snapshot, now)
                        except Conflict:
                            value['state'] = 'RESEARCH_CAPACITY'
                        else:
                            value.update(last_job=job['job_id'], state='RESEARCH_QUEUED',
                                next_due=(now+timedelta(seconds=value['interval_seconds'])).isoformat())
                self.save(db, owner, value)
                db.commit()

    def apply(self, owner, job, advice, check):
        """Serialize pause vs admission; account transaction performs final version/news checks."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            value = self.read(db, owner, job['account_id'])
            if (not value['enabled'] or value['last_job'] != job['job_id']
                    or value['epoch'] != job['snapshot'].get('automation', {}).get('epoch')):
                return None
            if value['receipt'] and value['receipt'].get('job_id') == job['job_id']:
                return value['receipt']
            now = self.service.clock()
            account = self.service.accounts.read(owner, job['account_id'])
            original = self.service.accounts.decision_request(owner, job['account_id'], job['job_id'])
            if original is not None:
                # Paper admission may have committed before this sidecar receipt did.
                # Recover the exact proposal clock; never create a fresh decision.
                restored = self.service.accounts.submit_ai(owner, job['account_id'], Proposal.model_validate(original),
                    account['revision'], now, self.service.news(account['config']['instrument'], now))
                receipt = dict(job_id=job['job_id'], request_id=job['job_id'], at=original['available_at'],
                    **restored['decision_result'], account_revision=restored['revision'], fills_count=restored['fills_count'])
                value.update(receipt=receipt, state=receipt['reason'])
                self.save(db, owner, value)
                db.commit()
                return receipt
            check = assess(job['snapshot'], advice, account, now=now,
                           news=self.service.news(account['config']['instrument'], now))
            reason = check['reason']
            receipt = dict(job_id=job['job_id'], request_id=job['job_id'], reason=reason,
                           status='REJECTED', at=now.isoformat())
            if reason == 'HOLD':
                receipt['status'] = 'HOLD'
            elif reason in ('SHADOW_READY', 'SHADOW_REDUCE_ONLY'):
                config = Config.model_validate(account['config'])
                if now + timedelta(seconds=config.decision_ttl_seconds) > advice.valid_until:
                    receipt['reason'] = 'ADVICE_TOO_CLOSE_TO_EXPIRY'
                elif job['account_id'] not in self.service.runner.accounts or not self.service.status()['model_configured']:
                    receipt['reason'] = 'MODEL_OR_ACCOUNT_NOT_AUTHORIZED'
                else:
                    candidate = advice.candidate
                    proposal = Proposal(request_id=job['job_id'], target=candidate.target, available_at=now,
                        quantity=candidate.quantity, stop_loss=candidate.stop_loss, take_profit=candidate.take_profit)
                    try:
                        result = self.service.accounts.submit_ai(owner, job['account_id'], proposal,
                            account['revision'], now, self.service.news(account['config']['instrument'], now))
                    except Conflict:
                        receipt['reason'] = 'ACCOUNT_CHANGED'
                    else:
                        receipt.update(result['decision_result'], account_revision=result['revision'], fills_count=result['fills_count'])
            value.update(receipt=receipt, state=receipt['reason'])
            self.save(db, owner, value)
            db.commit()
            return receipt
