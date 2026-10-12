"""Global credential revocation for existing scoped requested-Paper commands."""
from functools import wraps
from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from core.paper import segmented_revocations as revocations

HEADERS={'Cache-Control':'no-store'}
ACTIONS={
    'control-commands':None,
    'finalization-commands':'VOID_UNSUBMITTED',
    'exposure-captures':'READ_REQUESTED_EXPOSURE',
    'inbox-commands':'STAGE_LOCAL_INPUT',
    'inbox-application-commands':'APPLY_LOCAL_INPUT',
    'inbox-drain-commands':'DRAIN_LOCAL_INBOX',
    'inbox-captures':'READ_LOCAL_INBOX',
    'health-enrollment-commands':'ENROLL_HEALTH',
    'health-captures':'READ_HEALTH',
    'enrollment-commands':'ENROLL',
    'preparation-previews':'PREVIEW_PREPARE',
    'preparation-commands':'PREPARE',
    'event-previews':'PREVIEW_EVENT',
    'source-commands':'INGEST_EVENT',
    'ownership-commands':'CLAIM_OWNERSHIP',
    'owned-source-commands':'DELIVER_OWNED_EVENT',
    'dispatch-queries':'QUERY_DISPATCH',
    'assessment-commands':'RECORD_ASSESSMENT',
    'assessment-exports':'READ_ASSESSMENTS',
    'pool-preparation-previews':'PREVIEW_POOL_PREPARE',
    'pool-preparation-commands':'POOL_PREPARE',
    'pool-risk-enrollment-commands':'ENROLL_POOL_RISK',
    'pool-captures':'READ_POOL',
}


class OperatorRouter(APIRouter):
    def __init__(self,engine,settings):
        super().__init__()
        self._engine=engine
        self._settings=settings

    def _grant(self,name,value):
        settings=self._settings
        if settings is None or settings.paper_operator_token is None:
            raise HTTPException(503,'Paper operator commands are disabled',headers=HEADERS)
        action=value.action if name=='control-commands' else ACTIONS[name]
        if action not in settings.paper_operator_actions:
            raise HTTPException(403,'Paper operator grant denies this command',headers=HEADERS)
        if name.startswith('pool-'):
            if value.pool_id not in settings.paper_operator_pool_ids:
                raise HTTPException(403,'Explicit Paper pool scope denied',headers=HEADERS)
            identity=getattr(value,'account_id',None)
            accounts=[identity] if identity is not None else []
        else:
            accounts=value.account_ids if name=='exposure-captures' else [value.account_id]
        if any(identity not in settings.paper_operator_accounts for identity in accounts):
            raise HTTPException(403,'Paper operator grant denies this command',headers=HEADERS)

    def add_api_route(self,path,endpoint,**kwargs):
        prefix='/api/v1/paper-requested/'
        name=path[len(prefix):] if path.startswith(prefix) else None
        if name not in ACTIONS or set(kwargs.get('methods') or [])!={'POST'}:
            raise ValueError('Explicit protected operator route/action registration required')
        @wraps(endpoint)
        def fenced(*args,**values):
            value=values.get('value',args[0] if args else None)
            self._grant(name,value)
            token=self._settings.paper_operator_token.get_secret_value()
            try:
                # Only global */* credential denies apply to the legacy namespace.
                # Hold the same shared database fence through the financial call.
                with revocations.guard(self._engine,token,'*','*'):
                    return endpoint(*args,**values)
            except revocations.Revoked:
                raise HTTPException(401,'Paper operator authentication required',headers={**HEADERS,'WWW-Authenticate':'Bearer'})
            except SQLAlchemyError:
                raise HTTPException(503,'Operator revocation state temporarily unavailable',headers=HEADERS)
            except (ValueError,TypeError,KeyError):
                raise HTTPException(409,'Operator revocation state cannot be verified',headers=HEADERS)
        return super().add_api_route(path,fenced,**kwargs)
