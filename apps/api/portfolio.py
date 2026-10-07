from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from core.portfolio import history, continuity, sampled, risk_history
from pydantic import BaseModel, ConfigDict, Field, model_validator
from core.portfolio.valuation import Limits, capture, MissingAccount


class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    session_ids:list[UUID]=Field(min_length=1,max_length=8)
    limits:Limits=Field(default_factory=Limits)

    @model_validator(mode='after')
    def distinct(self):
        if len(set(self.session_ids))!=len(self.session_ids):raise ValueError('Duplicate accounts')
        return self


class CreateScenario(BaseModel):
    model_config=ConfigDict(extra="forbid")
    request_id:UUID
    definition:history.Definition

class CaptureScenario(BaseModel):
    model_config=ConfigDict(extra="forbid")
    request_id:UUID

def router_for(engine):
    router=APIRouter()
    @router.post('/api/v1/portfolio/valuation')
    def valuation(request:Request):
        try:return capture(engine,[str(id) for id in request.session_ids],request.limits)
        except MissingAccount:raise HTTPException(404,'Selected realtime account missing; historical accounts cannot be combined')
        except (ValueError,ArithmeticError,KeyError,TypeError):raise HTTPException(409,'Portfolio snapshot cannot be verified')
    def checked(operation):
        try:return operation()
        except history.Capacity as error:raise HTTPException(429,str(error))
        except history.Conflict as error:raise HTTPException(409,str(error))
        except MissingAccount:raise HTTPException(404,'Scenario, snapshot or account missing')
        except (ValueError,ArithmeticError,KeyError,TypeError):raise HTTPException(409,'History cannot be verified')
    @router.post('/api/v1/portfolio/scenarios')
    def create_scenario(request:CreateScenario):
        return checked(lambda:history.create(engine,str(request.request_id),request.definition))
    @router.get('/api/v1/portfolio/scenarios')
    def list_scenarios(limit:int=Query(20,ge=1,le=20),cursor:str|None=None):
        return checked(lambda:history.listing(engine,limit=limit,cursor=cursor))
    @router.get('/api/v1/portfolio/scenarios/{id}')
    def read_scenario(id:UUID):return checked(lambda:history.read(engine,str(id)))
    @router.post('/api/v1/portfolio/scenarios/{id}/snapshots')
    def capture_scenario(id:UUID,request:CaptureScenario):
        return checked(lambda:history.save(engine,str(id),str(request.request_id)))
    @router.get('/api/v1/portfolio/scenarios/{id}/snapshots')
    def list_snapshots(id:UUID,limit:int=Query(20,ge=1,le=20),cursor:str|None=None,status:str|None=None):
        return checked(lambda:history.snapshots(engine,str(id),limit=limit,cursor=cursor,status=status))
    @router.get('/api/v1/portfolio/scenarios/{id}/snapshots/{snapshot_id}')
    def read_snapshot(id:UUID,snapshot_id:UUID):return checked(lambda:history.saved(engine,str(id),str(snapshot_id)))
    @router.get('/api/v1/portfolio/scenarios/{id}/analysis')
    def analyze_scenario(id:UUID,limit:int=Query(8,ge=1,le=8)):
        return checked(lambda:continuity.capture(engine,str(id),limit=limit))
    @router.get('/api/v1/portfolio/scenarios/{id}/sampled')
    def sampled_scenario(id:UUID,limit:int=Query(8,ge=1,le=8)):
        return checked(lambda:sampled.capture(engine,str(id),limit=limit))
    @router.get('/api/v1/portfolio/scenarios/{id}/risk')
    def risk_scenario(id:UUID,limit:int=Query(8,ge=1,le=8)):
        return checked(lambda:risk_history.capture(engine,str(id),limit=limit))
    return router
