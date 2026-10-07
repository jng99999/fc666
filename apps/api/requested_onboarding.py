"""Scoped enrollment, preview and local preparation; no source/venue writes."""
from typing import Literal
from fastapi import Depends,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from sqlalchemy.exc import SQLAlchemyError
from core.paper import requested_controls as controls,requested_journal as journal,requested_preview as preview,requested_preparation as preparation,requested_event_preview as event_preview


class Rules(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    tick_size:str=Field(min_length=1,max_length=96)
    quantity_step:str=Field(min_length=1,max_length=96)
    min_quantity:str=Field(min_length=1,max_length=96)
    min_notional:str=Field(min_length=1,max_length=96)


class Limits(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    allowed_sides:list[Literal['BUY','SELL']]=Field(max_length=2)
    max_order_quantity:str=Field(min_length=1,max_length=96)
    max_request_notional:str=Field(min_length=1,max_length=96)
    max_buy_inventory_quantity:str=Field(min_length=1,max_length=96)
    max_fee_rate:str=Field(min_length=1,max_length=96)
    min_cash_after_buy:str=Field(min_length=1,max_length=96)


class Enrollment(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(min_length=1,max_length=128)
    command_id:str=Field(min_length=1,max_length=128)
    expected_financial_revision:StrictInt=Field(ge=0)
    created_at:str=Field(min_length=1,max_length=64)
    limits:Limits


class Proposal(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(min_length=1,max_length=128)
    client_request_id:str=Field(min_length=1,max_length=128)
    expected_financial_revision:StrictInt=Field(ge=0)
    expected_control_revision:StrictInt=Field(ge=1)
    side:Literal['BUY','SELL']
    requested_quantity:str=Field(min_length=1,max_length=96)
    limit_price:str=Field(min_length=1,max_length=96)
    max_fee_rate:str=Field(min_length=1,max_length=96)
    rules:Rules
    created_at:str=Field(min_length=1,max_length=64)


class Preparation(Proposal):
    preview_sha256:str=Field(pattern=r"^[0-9a-f]{64}$")


class LocalSource(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:Literal['LOCAL_PAPER_OPERATOR_INPUT']
    source_id:str=Field(min_length=1,max_length=128)


class SourceEvent(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    event_id:str=Field(min_length=1,max_length=128)
    request_id:str=Field(pattern=r'^[0-9a-f]{64}$')
    sequence:StrictInt=Field(ge=0,lt=1000)
    received_at:str=Field(min_length=1,max_length=64)
    kind:Literal['SUBMIT','ACK','UNKNOWN_SUBMISSION','CANCEL_REQUEST','CANCEL_ACK','REJECT','FILL','RECEIPT','SEAL']
    payload:dict=Field(max_length=6)


class EventProposal(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(min_length=1,max_length=128)
    request_id:str=Field(pattern=r'^[0-9a-f]{64}$')
    expected_financial_revision:StrictInt=Field(ge=1)
    expected_control_revision:StrictInt=Field(ge=1)
    source:LocalSource
    event:SourceEvent


def add_onboarding(router,engine,settings,authenticate):
    def grant(account_id,action):
        if account_id not in settings.paper_operator_accounts or action not in settings.paper_operator_actions:
            raise HTTPException(403,'Paper operator grant denies this command',headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/enrollment-commands',dependencies=[Depends(authenticate)])
    def enroll(value:Enrollment):
        grant(value.account_id,'ENROLL')
        try:
            policy=controls.policy(value.account_id,value.limits.model_dump())
            result=controls.enroll(engine,value.account_id,policy,value.command_id,value.created_at,expected_financial_revision=value.expected_financial_revision)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper enrollment conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper enrollment temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse({'version':'paper-requested-enrollment-command-result-v1','accepted_enrollment':result['records'][0],
                             'controls':result,'external_submission_allowed':False},headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/preparation-previews',dependencies=[Depends(authenticate)])
    def inspect(value:Proposal):
        grant(value.account_id,'PREVIEW_PREPARE')
        try:result=preview.capture(engine,value.model_dump())
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper preview conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper preview temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/preparation-commands',dependencies=[Depends(authenticate)])
    def prepare(value:Preparation):
        grant(value.account_id,'PREPARE')
        proposal=value.model_dump();digest=proposal.pop('preview_sha256')
        try:result=preparation.prepare(engine,proposal,digest)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper preparation conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper preparation temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/event-previews',dependencies=[Depends(authenticate)])
    def inspect_event(value:EventProposal):
        grant(value.account_id,'PREVIEW_EVENT')
        try:result=event_preview.capture(engine,value.model_dump())
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper event preview conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper event preview temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
