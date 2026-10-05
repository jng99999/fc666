from uuid import UUID
from datetime import datetime
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from apps.api.research import prepare,RunRequest
from core.replay import sessions


class ReplayRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    symbol: Literal['BTCUSDT','ETHUSDT']='BTCUSDT'
    timeframe: Literal['1m','5m','15m','1h','4h','1d']='1h'
    limit: int=Field(default=120,ge=2,le=1000,strict=True)
    period: int=Field(default=20,ge=2,le=500,strict=True)
    as_of: datetime|None=None

    @field_validator('as_of')
    @classmethod
    def cutoff(cls,value):return RunRequest.cutoff(value)


class Command(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision: int=Field(ge=0,le=2000000000,strict=True)
    action: Literal['step','reset']
    count: int=Field(default=1,ge=1,le=10,strict=True)

    @model_validator(mode='after')
    def reset_count(self):
        if self.action=='reset' and self.count!=1:raise ValueError('Reset count must be one')
        return self


def router_for(engine):
    router=APIRouter()
    def error(exception):
        if isinstance(exception,KeyError):raise HTTPException(404,'Replay session not found')
        if isinstance(exception,sessions.Conflict):raise HTTPException(409,'Replay revision conflict; read current state before continuing')
        raise HTTPException(409,str(exception))

    @router.post('/api/v1/replay/sessions',status_code=201)
    def create(request:ReplayRequest):
        bars,instrument,frozen=prepare(engine,request)
        try:return sessions.create(engine,frozen,bars,instrument)
        except ValueError as exception:error(exception)

    @router.get('/api/v1/replay/sessions/{session_id}')
    def read(session_id:UUID):
        try:return sessions.read(engine,str(session_id))
        except (KeyError,ValueError) as exception:error(exception)

    @router.post('/api/v1/replay/sessions/{session_id}/command')
    def command(session_id:UUID,request:Command):
        try:return sessions.command(engine,str(session_id),request.expected_revision,request.action,request.count)
        except (KeyError,ValueError,sessions.Conflict) as exception:error(exception)
    return router
