from uuid import UUID
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator
from apps.api.research import RunRequest, prepare
from apps.api.replay import Command
from core.paper.ledger import RiskLimits
from core.paper import sessions, streams


class PaperRequest(RunRequest):
    risk: RiskLimits = Field(default_factory=RiskLimits)


class PaperCommand(Command):
    action: Literal['step', 'reset', 'halt']

    @model_validator(mode='after')
    def halt_count(self):
        if self.action == 'halt' and self.count != 1:
            raise ValueError('Halt count must be one')
        return self


class StreamRequest(PaperRequest):
    limit: int = Field(default=120, ge=2, le=500, strict=True)
    max_lag_seconds: int = Field(default=15, ge=1, le=60, strict=True)

    @model_validator(mode='after')
    def realtime_cutoff(self):
        if self.as_of is not None:
            raise ValueError('Realtime account uses creation time; historical as_of unavailable')
        return self


class StreamCommand(Command):
    action: Literal['pause', 'resume', 'halt', 'stop']

    @model_validator(mode='after')
    def one(self):
        if self.count != 1:
            raise ValueError('Realtime command count must be one')
        return self


def router_for(engine):
    router = APIRouter()

    def error(exception):
        if isinstance(exception, KeyError):
            raise HTTPException(404, 'Paper account not found')
        if isinstance(exception, sessions.Conflict):
            raise HTTPException(409, 'Paper revision conflict; read current state before continuing')
        raise HTTPException(409, str(exception))

    @router.post('/api/v1/paper/sessions', status_code=201)
    def create(request: PaperRequest):
        bars, instrument, frozen = prepare(engine, request)
        try:
            return sessions.create(engine, frozen, bars, instrument)
        except ValueError as exception:
            error(exception)

    @router.get('/api/v1/paper/sessions/{session_id}')
    def read(session_id: UUID):
        try:
            return sessions.read(engine, str(session_id))
        except (KeyError, ValueError) as exception:
            error(exception)

    @router.post('/api/v1/paper/sessions/{session_id}/command')
    def command(session_id: UUID, request: PaperCommand):
        try:
            return sessions.command(engine, str(session_id), request.expected_revision, request.action, request.count)
        except (KeyError, ValueError, sessions.Conflict) as exception:
            error(exception)
    @router.get('/api/v1/paper/status')
    def worker_status():
        try:ready=streams.healthy(engine)
        except Exception:ready=False
        return {'status':'healthy' if ready else 'unavailable','trading_enabled':False}

    @router.post('/api/v1/paper/streams',status_code=201)
    def create_stream(request:StreamRequest):
        bars,instrument,frozen=prepare(engine,request)
        try:return streams.create(engine,frozen,bars,instrument)
        except streams.Capacity:raise HTTPException(429,'Paper capacity full (20 running/paused accounts)')
        except ValueError as exception:error(exception)

    @router.get('/api/v1/paper/streams/{session_id}')
    def read_stream(session_id:UUID):
        try:return streams.read(engine,str(session_id))
        except (KeyError,ValueError) as exception:error(exception)

    @router.post('/api/v1/paper/streams/{session_id}/command')
    def command_stream(session_id:UUID,request:StreamCommand):
        try:return streams.command(engine,str(session_id),request.expected_revision,request.action)
        except (KeyError,ValueError,streams.Conflict) as exception:error(exception)
    return router
