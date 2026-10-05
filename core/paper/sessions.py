"""Frozen historical paper accounts with transactional cursor and persisted ledger."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy.orm import Session
from core.models import Candle, Instrument
from core.backtest.spot import BacktestConfig, digest
from core.replay.sessions import validate, Conflict
from core.paper.ledger import VERSION, RiskLimits, evaluate
from core.storage.models import PaperSessionRecord as Paper


def inputs(snapshot):
    if snapshot['version'] != VERSION or digest({k:v for k,v in snapshot.items() if k != 'sha256'}) != snapshot['sha256']:
        raise ValueError('Paper snapshot integrity failed')
    bars = [Candle.model_validate(b) for b in snapshot['dataset']]
    instrument = Instrument.model_validate(snapshot['instrument'])
    validate(bars, instrument)
    request = snapshot['request']
    return bars, instrument, BacktestConfig.model_validate(request['config']), RiskLimits.model_validate(request['risk'])


def computed(record):
    bars, instrument, config, limits = inputs(record.snapshot)
    if not 0 <= record.cursor <= len(bars):
        raise ValueError('Invalid paper cursor')
    request = record.snapshot['request']
    return evaluate(bars[:record.cursor], instrument, config, limits, strategy_id=request['strategy'], parameters=request['parameters'], snapshot_sha256=record.snapshot['sha256'], halt_at=record.halt_at, ended=record.cursor==len(bars))


def visible(record):
    ledger = computed(record)
    if ledger != record.ledger:
        raise ValueError('Paper ledger integrity failed')
    snapshot = record.snapshot
    request = snapshot['request']
    reached = snapshot['dataset'][:record.cursor]
    clock = reached[-1]['close_time'] if reached else snapshot['dataset'][0]['open_time']
    return {'session_id': record.session_id, 'revision': record.revision, 'cursor': record.cursor, 'total': len(snapshot['dataset']), 'status': 'ENDED' if record.cursor == len(snapshot['dataset']) else 'READY', 'clock': clock,
            'manifest': {'version': VERSION, 'snapshot_sha256': snapshot['sha256'], 'symbol': request['symbol'], 'timeframe': request['timeframe'], 'as_of': request['as_of'], 'strategy': request['strategy'], 'strategy_version': 1, 'parameters': request['parameters'], 'config': request['config'], 'risk': request['risk'], 'instrument': snapshot['instrument'], 'start': snapshot['dataset'][0]['open_time'], 'end': snapshot['dataset'][-1]['close_time']},
            'candles': reached, **ledger, 'trading_enabled': False, 'mode': 'HISTORICAL_PAPER'}


def create(engine, request, bars, instrument):
    validate(bars, instrument)
    snapshot = {'version': VERSION, 'request': request, 'dataset': [bar.model_dump(mode='json') for bar in bars], 'instrument': instrument.model_dump(mode='json')}
    snapshot['sha256'] = digest(snapshot)
    stamp = datetime.now(timezone.utc)
    with Session(engine) as session, session.begin():
        record = Paper(session_id=str(uuid4()), created_at=stamp, updated_at=stamp, snapshot=snapshot, cursor=0, revision=0, halt_at=None)
        record.ledger = computed(record)
        response = visible(record)
        session.add(record)
        return response


def read(engine, session_id):
    with Session(engine) as session:
        record = session.get(Paper, session_id)
        if record is None:
            raise KeyError(session_id)
        return visible(record)


def command(engine, session_id, expected_revision, action, count=1):
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise ValueError('Nonnegative integer revision required')
    if action not in ['step', 'reset', 'halt'] or isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10 or (action != 'step' and count != 1):
        raise ValueError('Require step(1..10), reset(1) or halt(1)')
    with Session(engine) as session, session.begin():
        record = session.get(Paper, session_id, with_for_update=True)
        if record is None:
            raise KeyError(session_id)
        if record.revision != expected_revision:
            raise Conflict()
        before = visible(record)
        previous = (record.cursor, record.halt_at)
        if action == 'step':
            record.cursor = min(record.cursor + count, before['total'])
        elif action == 'reset':
            record.cursor = 0
            record.halt_at = None
        elif record.halt_at is None:
            record.halt_at = record.cursor
        if (record.cursor, record.halt_at) != previous:
            record.ledger = computed(record)
            record.revision += 1
            record.updated_at = datetime.now(timezone.utc)
        return visible(record)
