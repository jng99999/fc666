"""Scoped single-operator evidence and root enrollment; no financial write API."""
from hmac import compare_digest
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from core.paper import segmented_ledger as ledger, segmented_worker as worker
from core.paper import segmented_ledger_archive as financial, segmented_operational_archive as operational

HEADERS = {'Cache-Control': 'no-store'}


class Enrollment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    ledger_id: str = Field(min_length=11, max_length=128, pattern=r'^segmented:.+$')
    command_id: str = Field(min_length=1, max_length=128)
    expected_root_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def router_for(engine, settings):
    router = APIRouter()
    bearer = HTTPBearer(auto_error=False)

    def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if settings.paper_operator_token is None:
            raise HTTPException(503, 'Paper operator access is disabled', headers=HEADERS)
        if credentials is None or not compare_digest(credentials.credentials.encode(), settings.paper_operator_token.get_secret_value().encode()):
            raise HTTPException(401, 'Paper operator authentication required',
                                headers={**HEADERS, 'WWW-Authenticate': 'Bearer'})

    def grant(identity, action):
        if identity not in settings.paper_operator_accounts or action not in settings.paper_operator_actions:
            raise HTTPException(403, 'Paper operator grant denies this operation', headers=HEADERS)

    def perform(call):
        try:
            return call()
        except ledger.MissingLedger:
            raise HTTPException(404, 'Independent local ledger not found', headers=HEADERS)
        except (ValueError, ArithmeticError, TypeError, KeyError):
            raise HTTPException(409, 'Independent local evidence conflicts or cannot be verified', headers=HEADERS)
        except SQLAlchemyError:
            raise HTTPException(503, 'Independent local ledger temporarily unavailable', headers=HEADERS)

    @router.get('/api/v1/paper-segmented/evidence', dependencies=[Depends(authenticate)])
    def evidence(ledger_id: str = Query(..., min_length=11, max_length=128, pattern=r'^segmented:.+$'),
                 scope: Literal['financial', 'operational'] = 'financial'):
        grant(ledger_id, 'READ_SEGMENTED_EVIDENCE')
        archive = operational if scope == 'operational' else financial
        return JSONResponse(perform(lambda: archive.capture(engine, ledger_id)), headers=HEADERS)

    @router.post('/api/v1/paper-segmented/worker-enrollments', dependencies=[Depends(authenticate)])
    def enroll(value: Enrollment):
        grant(value.ledger_id, 'ENROLL_SEGMENTED_WORKER')
        accepted = perform(lambda: worker.enroll(engine, value.ledger_id, value.command_id,
                                                expected_root_sha256=value.expected_root_sha256))
        return JSONResponse({'version': 'paper-segmented-worker-enrollment-command-result-v1',
                             'accepted_enrollment': accepted, 'operator_worker_binding_supported': False,
                             'external_submission_allowed': False}, headers=HEADERS)

    return router
