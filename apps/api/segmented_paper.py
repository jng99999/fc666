"""Scoped single-operator evidence and root enrollment; no financial write API."""
from contextlib import contextmanager
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from apps.api.segmented_access import Access, Denied
from core.paper import segmented_ledger as ledger, segmented_worker as worker
from core.paper import segmented_ledger_archive as financial, segmented_operational_archive as operational

HEADERS = {'Cache-Control': 'no-store'}


class Enrollment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    ledger_id: str = Field(min_length=11, max_length=128, pattern=r'^segmented:.+$')
    command_id: str = Field(min_length=1, max_length=128)
    expected_root_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def router_for(engine, settings, *, access=None):
    router = APIRouter()
    access = access or Access(settings,engine=engine)
    bearer = HTTPBearer(auto_error=False)

    def deny(error):
        details = {503: 'Paper operator access is disabled', 401: 'Paper operator authentication required',
                   403: 'Paper operator grant denies this operation'}
        raise HTTPException(error.status, details[error.status],
                            headers={**HEADERS, **({'WWW-Authenticate': 'Bearer'} if error.status == 401 else {})})

    def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        try:
            access.authenticate(credentials.credentials if credentials else None)
        except Denied as error:
            deny(error)
        return credentials.credentials

    @contextmanager
    def grant(token, identity, action):
        try:
            with access.operation(token, identity, action):
                yield
        except Denied as error:
            deny(error)
        except SQLAlchemyError:
            raise HTTPException(503, 'Operator revocation state temporarily unavailable', headers=HEADERS)
        except (ValueError, TypeError, KeyError):
            raise HTTPException(409, 'Operator revocation state cannot be verified', headers=HEADERS)

    def perform(call):
        try:
            return call()
        except ledger.MissingLedger:
            raise HTTPException(404, 'Independent local ledger not found', headers=HEADERS)
        except (ValueError, ArithmeticError, TypeError, KeyError):
            raise HTTPException(409, 'Independent local evidence conflicts or cannot be verified', headers=HEADERS)
        except SQLAlchemyError:
            raise HTTPException(503, 'Independent local ledger temporarily unavailable', headers=HEADERS)

    @router.get('/api/v1/paper-segmented/evidence')
    def evidence(ledger_id: str = Query(..., min_length=11, max_length=128, pattern=r'^segmented:.+$'),
                 scope: Literal['financial', 'operational'] = 'financial', token: str = Depends(authenticate)):
        with grant(token, ledger_id, 'READ_SEGMENTED_EVIDENCE'):
            archive = operational if scope == 'operational' else financial
            return JSONResponse(perform(lambda: archive.capture(engine, ledger_id)), headers=HEADERS)

    @router.post('/api/v1/paper-segmented/worker-enrollments')
    def enroll(value: Enrollment, token: str = Depends(authenticate)):
        with grant(token, value.ledger_id, 'ENROLL_SEGMENTED_WORKER'):
            accepted = perform(lambda: worker.enroll(engine, value.ledger_id, value.command_id,
                                                    expected_root_sha256=value.expected_root_sha256))
            return JSONResponse({'version': 'paper-segmented-worker-enrollment-command-result-v1',
                                 'accepted_enrollment': accepted, 'operator_worker_binding_supported': False,
                                 'external_submission_allowed': False}, headers=HEADERS)

    return router
