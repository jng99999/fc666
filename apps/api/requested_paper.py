"""Read-only complete explicit-request Paper journal; no execution commands."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from core.paper import requested_inspection as inspection, requested_journal as journal


def router_for(engine):
    router=APIRouter()

    @router.get('/api/v1/paper-requested/journal')
    def inspect(account_id:str=Query(...,min_length=1,max_length=128)):
        try:
            report=inspection.capture(engine,account_id)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper journal cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper journal temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(content=report,headers={'Cache-Control':'no-store'})

    return router
