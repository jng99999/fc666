"""Bounded account discovery and append-only control records, without user identity claims."""
import base64
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import select, tuple_, func, case
from sqlalchemy.orm import Session
from core.storage.models import PaperSessionRecord, PaperStreamRecord, PaperControlRecord

MODELS = {'sessions': PaperSessionRecord, 'streams': PaperStreamRecord}
STATUSES = {'sessions': {'READY', 'ENDED'}, 'streams': {'RUNNING', 'PAUSED', 'STOPPED', 'BLOCKED', 'LIMIT_REACHED'}}


def state(record, kind):
    return {'status': record.status if kind == 'streams' else ('ENDED' if record.cursor == len(record.snapshot['dataset']) else 'READY'),
            'position': len(record.observations) if kind == 'streams' else record.cursor, 'halt_at': record.halt_at}


def record_control(session, record, kind, action, count, expected_revision, before_revision, before):
    outcome = 'CONFLICT' if expected_revision != before_revision else 'APPLIED' if record.revision != before_revision else 'NOOP'
    session.add(PaperControlRecord(event_id=str(uuid4()), recorded_at=datetime.now(timezone.utc), kind=kind,
        session_id=record.session_id, action=action, count=count, expected_revision=expected_revision,
        before_revision=before_revision, after_revision=record.revision, outcome=outcome, before=before, after=state(record, kind)))


def cursor_for(row, time_field, id_field):
    value = [getattr(row, time_field).isoformat(), getattr(row, id_field)]
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def decode_cursor(cursor):
    try:
        if len(cursor) > 256: raise ValueError()
        value = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
        if not isinstance(value, list) or len(value) != 2: raise ValueError()
        stamp = datetime.fromisoformat(value[0])
        if stamp.tzinfo is None: raise ValueError()
        return stamp, str(UUID(value[1]))
    except (ValueError, TypeError, KeyError) as exception:
        raise ValueError('Invalid pagination cursor') from exception


def page(session, model, query, limit, cursor, time_field, id_field):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError('Page limit must be 1..20')
    stamp, identifier = getattr(model, time_field), getattr(model, id_field)
    if cursor is not None: query = query.where(tuple_(stamp, identifier) < decode_cursor(cursor))
    rows = list(session.scalars(query.order_by(stamp.desc(), identifier.desc()).limit(limit + 1)))
    return rows[:limit], cursor_for(rows[limit - 1], time_field, id_field) if len(rows) > limit else None


def listing(engine, kind, *, limit=20, cursor=None, status=None):
    from core.paper import sessions, streams
    model = MODELS[kind]
    query = select(model)
    if status is not None:
        if status not in STATUSES[kind]: raise ValueError('Unsupported account status')
        expression = model.status if kind == 'streams' else case((model.cursor == func.json_array_length(model.snapshot['dataset']), 'ENDED'), else_='READY')
        query = query.where(expression == status)
    with Session(engine) as session:
        rows, next_cursor = page(session, model, query, limit, cursor, 'created_at', 'session_id')
        items = []
        for row in rows:
            request = row.snapshot['request']
            try:
                visible = (streams if kind == 'streams' else sessions).visible(row)
                account = visible['account']; integrity = 'VERIFIED'
            except ValueError:
                account = None; integrity = 'INVALID'
            items.append({'session_id': row.session_id, 'created_at': row.created_at.isoformat(), 'updated_at': row.updated_at.isoformat(),
                'revision': row.revision, **state(row, kind), 'symbol': request['symbol'], 'timeframe': request['timeframe'],
                'strategy': request['strategy'], 'integrity': integrity, 'account': account,
                'feed': row.feed if kind == 'streams' else None})
        return {'items': items, 'next_cursor': next_cursor, 'trading_enabled': False}


def controls(engine, kind, session_id, *, limit=20, cursor=None):
    with Session(engine) as session:
        if session.get(MODELS[kind], session_id) is None: raise KeyError(session_id)
        model = PaperControlRecord
        query = select(model).where(model.kind == kind, model.session_id == session_id)
        rows, next_cursor = page(session, model, query, limit, cursor, 'recorded_at', 'event_id')
        fields = ['event_id', 'action', 'count', 'expected_revision', 'before_revision', 'after_revision', 'outcome', 'before', 'after']
        return {'items': [{**{field: getattr(row, field) for field in fields}, 'recorded_at': row.recorded_at.isoformat(),
                          'actor': 'UNAUTHENTICATED_LOCAL'} for row in rows], 'next_cursor': next_cursor, 'trading_enabled': False,
                'coverage': 'CONTROL_REQUESTS_SINCE_SCHEMA_0008'}
