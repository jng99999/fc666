"""Scoped local Paper commands; no venue transport."""
from typing import Literal
from core.portfolio import requested_exposure
from fastapi import Depends,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from sqlalchemy.exc import SQLAlchemyError
from core.paper import requested_controls as controls,requested_journal as journal,requested_preview as preview,requested_preparation as preparation,requested_event_preview as event_preview,requested_sources as sources,requested_ownership as ownership,requested_dispatch_query as dispatch_query,requested_attempts as attempts,shared_capital_pool as capital_pool,shared_capital_admission as capital_admission,requested_health as health,requested_inbox as inbox


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


class SourceCommand(EventProposal):
    preview_sha256:str=Field(pattern=r"^[0-9a-f]{64}$")


class OwnershipCommand(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(min_length=1,max_length=128)
    request_id:str=Field(pattern=r'^[0-9a-f]{64}$')
    owner:str=Field(min_length=1,max_length=128)
    ttl_seconds:StrictInt=Field(ge=1,le=60)
    expected_financial_revision:StrictInt=Field(ge=1)
    expected_control_revision:StrictInt=Field(ge=1)
    expected_token:StrictInt=Field(ge=0,le=64)


class OwnedSourceCommand(SourceCommand):
    owner:str=Field(min_length=1,max_length=128)
    ownership_token:StrictInt=Field(ge=1,le=64)


class DispatchQuery(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(min_length=1,max_length=128)
    request_id:str=Field(pattern=r'^[0-9a-f]{64}$')
    client_id:str=Field(pattern=r'^[0-9a-f]{64}$')
    owner:str=Field(min_length=1,max_length=128)
    ownership_token:StrictInt=Field(ge=1,le=64)


class AssessmentRead(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(min_length=1,max_length=128)
    request_id:str=Field(pattern=r'^[0-9a-f]{64}$')


class AssessmentCommand(DispatchQuery):
    attempt_id:str=Field(min_length=1,max_length=128)
    failure:Literal['TIMEOUT','DISCONNECTED','EMPTY_RESULT','CONTRADICTORY_RESULT','UNSUPPORTED_TRANSPORT']
    expected_financial_revision:StrictInt=Field(ge=1)
    expected_control_revision:StrictInt=Field(ge=1)


class PoolRead(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    pool_id:str=Field(min_length=1,max_length=128)


class PoolProposal(Proposal):
    pool_id:str=Field(min_length=1,max_length=128)


class PoolPreparation(PoolProposal):
    local_preview_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')


class HealthRead(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(min_length=1,max_length=128)


class ExposureRead(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_ids:list[str]=Field(min_length=1,max_length=8)


class HealthEnrollment(HealthRead):
    expected_control_revision:StrictInt=Field(ge=1)
    expected_financial_revision:StrictInt=Field(ge=0,le=0)


def add_onboarding(router,engine,settings,authenticate,*,health_cache=None):
    def grant(account_id,action):
        if account_id not in settings.paper_operator_accounts or action not in settings.paper_operator_actions:
            raise HTTPException(403,'Paper operator grant denies this command',headers={'Cache-Control':'no-store'})

    def health_result(call):
        try:result=call()
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper health conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper health temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    def inbox_result(call,component="inbox"):
        try:result=call()
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,f'Explicit Paper {component} conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,f'Explicit Paper {component} temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/exposure-captures',dependencies=[Depends(authenticate)])
    def read_exposure(value:ExposureRead):
        for account in value.account_ids:grant(account,'READ_REQUESTED_EXPOSURE')
        return inbox_result(lambda:requested_exposure.capture(engine,value.account_ids),component="exposure")

    @router.post('/api/v1/paper-requested/inbox-commands',dependencies=[Depends(authenticate)])
    def stage_input(value:inbox.Stage):
        grant(value.account_id,'STAGE_LOCAL_INPUT')
        return inbox_result(lambda:inbox.stage(engine,value.model_dump()))

    @router.post('/api/v1/paper-requested/inbox-application-commands',dependencies=[Depends(authenticate)])
    def apply_input(value:inbox.Apply):
        grant(value.account_id,'APPLY_LOCAL_INPUT')
        return inbox_result(lambda:inbox.apply(engine,value.model_dump(),health_cache=health_cache))

    @router.post('/api/v1/paper-requested/inbox-captures',dependencies=[Depends(authenticate)])
    def read_inbox(value:HealthRead):
        grant(value.account_id,'READ_LOCAL_INBOX')
        return inbox_result(lambda:inbox.capture(engine,value.account_id))

    @router.post('/api/v1/paper-requested/health-enrollment-commands',dependencies=[Depends(authenticate)])
    def enroll_health(value:HealthEnrollment):
        grant(value.account_id,'ENROLL_HEALTH')
        def run():
            result=health.enroll(engine,value.account_id,value.expected_control_revision)
            return {'version':'paper-requested-health-enrollment-result-v1','policy':result,
                    'enrollment_committed':True,'external_submission_allowed':False}
        return health_result(run)

    @router.post('/api/v1/paper-requested/health-captures',dependencies=[Depends(authenticate)])
    def read_health(value:HealthRead):
        grant(value.account_id,'READ_HEALTH')
        return health_result(lambda:health.capture(engine,value.account_id))

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
        try:result=preparation.prepare(engine,proposal,digest,health_cache=health_cache)
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

    @router.post('/api/v1/paper-requested/source-commands',dependencies=[Depends(authenticate)])
    def accept_source(value:SourceCommand):
        grant(value.account_id,'INGEST_EVENT')
        proposal=value.model_dump();digest=proposal.pop('preview_sha256')
        try:result=sources.accept(engine,proposal,digest,health_cache=health_cache)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper source input conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper source input temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/ownership-commands',dependencies=[Depends(authenticate)])
    def claim_ownership(value:OwnershipCommand):
        grant(value.account_id,'CLAIM_OWNERSHIP')
        try:result=ownership.claim(engine,value.account_id,value.request_id,value.owner,value.ttl_seconds,
                                  expected_financial_revision=value.expected_financial_revision,
                                  expected_control_revision=value.expected_control_revision,expected_token=value.expected_token)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper ownership command conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper ownership command temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse({'version':'paper-requested-ownership-command-result-v1','accepted_claim':result,
                             'claim_committed':True,'external_submission_allowed':False},headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/owned-source-commands',dependencies=[Depends(authenticate)])
    def deliver_owned_source(value:OwnedSourceCommand):
        grant(value.account_id,'DELIVER_OWNED_EVENT')
        proposal=value.model_dump();digest=proposal.pop('preview_sha256');owner=proposal.pop('owner');token=proposal.pop('ownership_token')
        try:result=ownership.deliver(engine,proposal,digest,owner,token,health_cache=health_cache)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper owned source conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper owned source temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/dispatch-queries',dependencies=[Depends(authenticate)])
    def query_dispatch(value:DispatchQuery):
        grant(value.account_id,'QUERY_DISPATCH')
        try:result=dispatch_query.capture(engine,**value.model_dump())
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper dispatch query conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper dispatch query temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/assessment-commands',dependencies=[Depends(authenticate)])
    def record_assessment(value:AssessmentCommand):
        grant(value.account_id,'RECORD_ASSESSMENT')
        try:result=attempts.record(engine,value.model_dump())
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper assessment conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper assessment temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/assessment-exports',dependencies=[Depends(authenticate)])
    def read_assessments(value:AssessmentRead):
        grant(value.account_id,'READ_ASSESSMENTS')
        try:result=attempts.export(engine,value.account_id,value.request_id)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper assessment export conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper assessment export temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    def pool_grant(pool_id,action,account_id=None):
        if action not in settings.paper_operator_actions or pool_id not in settings.paper_operator_pool_ids or (account_id is not None and account_id not in settings.paper_operator_accounts):
            raise HTTPException(403,'Explicit Paper pool scope denied',headers={'Cache-Control':'no-store'})

    def pool_result(call):
        try:result=call()
        except capital_pool.PoolScopeDenied:
            raise HTTPException(403,'Explicit Paper pool scope denied',headers={'Cache-Control':'no-store'})
        except (capital_pool.MissingPool,journal.MissingAccount):
            raise HTTPException(404,'Explicit Paper pool or account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper pool operation conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper pool operation temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/paper-requested/pool-preparation-previews',dependencies=[Depends(authenticate)])
    def preview_pool_preparation(value:PoolProposal):
        pool_grant(value.pool_id,'PREVIEW_POOL_PREPARE',value.account_id)
        proposal=value.model_dump();pool_id=proposal.pop('pool_id')
        return pool_result(lambda:capital_admission.capture_preview(engine,pool_id,proposal,authorized_accounts=settings.paper_operator_accounts))

    @router.post('/api/v1/paper-requested/pool-preparation-commands',dependencies=[Depends(authenticate)])
    def prepare_pool(value:PoolPreparation):
        pool_grant(value.pool_id,'POOL_PREPARE',value.account_id)
        proposal=value.model_dump();pool_id=proposal.pop('pool_id');digest=proposal.pop('local_preview_sha256')
        return pool_result(lambda:capital_admission.prepare(engine,pool_id,proposal,digest,authorized_accounts=settings.paper_operator_accounts,health_cache=health_cache))

    @router.post('/api/v1/paper-requested/pool-captures',dependencies=[Depends(authenticate)])
    def read_pool(value:PoolRead):
        pool_grant(value.pool_id,'READ_POOL')
        return pool_result(lambda:capital_pool.capture(engine,value.pool_id,authorized_accounts=settings.paper_operator_accounts))
