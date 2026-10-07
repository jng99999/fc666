"""Explicit Paper inspection and scoped local commands; no venue execution."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from core.paper import requested_inspection as inspection, requested_journal as journal, requested_controls as controls,requested_sources as sources, requested_recovery as recovery, requested_ownership as ownership, requested_dispatch as dispatch


def router_for(engine,settings=None):
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

    @router.get('/api/v1/paper-requested/controls')
    def inspect_controls(account_id:str=Query(...,min_length=1,max_length=128)):
        try:report=controls.capture(engine,account_id)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper controls cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper controls temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(content=report,headers={'Cache-Control':'no-store'})

    @router.get('/api/v1/paper-requested/sources')
    def inspect_sources(account_id:str=Query(...,min_length=1,max_length=128)):
        try:report=sources.capture(engine,account_id)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper source evidence cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper source evidence temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(report,headers={'Cache-Control':'no-store'})

    @router.get('/api/v1/paper-requested/recovery')
    def inspect_recovery(account_id:str=Query(...,min_length=1,max_length=128)):
        try:report=recovery.capture(engine,account_id)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper recovery evidence cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper recovery temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(report,headers={'Cache-Control':'no-store'})

    @router.get('/api/v1/paper-requested/ownership')
    def inspect_ownership(account_id:str=Query(...,min_length=1,max_length=128)):
        try:report=ownership.capture(engine,account_id)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper ownership evidence cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper ownership temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(report,headers={'Cache-Control':'no-store'})

    @router.get('/api/v1/paper-requested/dispatches')
    def inspect_dispatches(account_id:str=Query(...,min_length=1,max_length=128)):
        try:report=dispatch.capture(engine,account_id)
        except journal.MissingAccount:
            raise HTTPException(404,'Explicit Paper account not found',headers={'Cache-Control':'no-store'})
        except (ValueError,ArithmeticError,TypeError,KeyError):
            raise HTTPException(409,'Explicit Paper dispatch evidence cannot be verified',headers={'Cache-Control':'no-store'})
        except SQLAlchemyError:
            raise HTTPException(503,'Explicit Paper dispatch temporarily unavailable',headers={'Cache-Control':'no-store'})
        return JSONResponse(report,headers={'Cache-Control':'no-store'})

    from apps.api.requested_commands import add_commands
    add_commands(router,engine,settings)
    return router
