"""Explicit single-operator local control capability; disabled by default."""
from hmac import compare_digest
from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.exc import SQLAlchemyError
from core.paper import requested_controls as controls, requested_journal as journal

VERSION='paper-requested-control-command-result-v1'


class ControlCommand(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(min_length=1,max_length=128)
    command_id:str=Field(min_length=1,max_length=128)
    expected_control_revision:StrictInt=Field(ge=1)
    expected_financial_revision:StrictInt=Field(ge=0)
    action:Literal['PAUSE','HALT','STOP','RESUME']
    created_at:str=Field(min_length=1,max_length=64)


def add_commands(router,engine,settings):
    bearer=HTTPBearer(auto_error=False)
    def authenticate(credentials:HTTPAuthorizationCredentials|None=Depends(bearer)):
        if settings is None or settings.paper_operator_token is None:
            raise HTTPException(503,'Paper operator commands are disabled',headers={'Cache-Control':'no-store'})
        if credentials is None or not compare_digest(credentials.credentials.encode(),settings.paper_operator_token.get_secret_value().encode()):
            raise HTTPException(401,'Paper operator authentication required',headers={'Cache-Control':'no-store','WWW-Authenticate':'Bearer'})

    @router.post('/api/v1/paper-requested/control-commands',dependencies=[Depends(authenticate)])
    def control_command(value:ControlCommand):
        if value.account_id not in settings.paper_operator_accounts or value.action not in settings.paper_operator_actions:
            raise HTTPException(403,'Paper operator grant denies this command',headers={'Cache-Control':'no-store'})
        try:
            view=controls.command(engine,value.account_id,value.command_id,value.expected_control_revision,value.action,value.created_at,
                                  expected_financial_revision=value.expected_financial_revision)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper command conflicts or cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper command temporarily unavailable',headers={'Cache-Control':'no-store'})
        accepted=next(record for record in view['records'] if record['command_id']==value.command_id)
        return JSONResponse({'version':VERSION,'accepted_command':accepted,'controls':view,'external_submission_allowed':False},headers={'Cache-Control':'no-store'})
