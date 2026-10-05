from uuid import UUID
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator
from apps.api.research import RunRequest, prepare
from apps.api.replay import Command
from core.paper.ledger import RiskLimits
from core.paper import sessions


class PaperRequest(RunRequest):
    risk: RiskLimits = Field(default_factory=RiskLimits)


class PaperCommand(Command):
    action: Literal['step', 'reset', 'halt']

    @model_validator(mode='after')
    def halt_count(self):
        if self.action == 'halt' and self.count != 1:
            raise ValueError('Halt count must be one')
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
    return router
