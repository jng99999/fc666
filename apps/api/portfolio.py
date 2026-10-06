from uuid import UUID
from fastapi import APIRouter, HTTPException
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


def router_for(engine):
    router=APIRouter()
    @router.post('/api/v1/portfolio/valuation')
    def valuation(request:Request):
        try:return capture(engine,[str(id) for id in request.session_ids],request.limits)
        except MissingAccount:raise HTTPException(404,'Selected realtime account missing; historical accounts cannot be combined')
        except (ValueError,ArithmeticError,KeyError,TypeError):raise HTTPException(409,'Portfolio snapshot cannot be verified')
    return router
