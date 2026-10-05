import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import create_engine, text
from core.storage.schema import SCHEMA_REVISION
from apps.api.settings import Settings
from apps.api.research import router_for as research_router
from apps.api.market import router_for, market_health

logger = logging.getLogger("fc666.api")

def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    engine = create_engine(config.database_url.get_secret_value(), pool_pre_ping=True,
                           connect_args={"connect_timeout": 2}, hide_parameters=True)
    cache = Redis.from_url(config.redis_url, socket_connect_timeout=2, socket_timeout=2)

    @asynccontextmanager
    async def lifespan(app):
        yield
        cache.close()
        engine.dispose()

    app = FastAPI(title="FC666", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.cache = cache
    app.include_router(router_for(engine,cache,config.redis_url))
    app.include_router(research_router(engine))

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        checks = {"database": False, "schema": False, "redis": False}
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                checks["database"] = True
                conn.execute(text("SELECT instrument_id FROM instruments LIMIT 0"))
                conn.execute(text("SELECT instrument_id FROM candles LIMIT 0"))
                conn.execute(text("SELECT revision_id FROM candle_revisions LIMIT 0"))
                conn.execute(text("SELECT job_id FROM research_jobs LIMIT 0"))
                conn.execute(text("SELECT worker_id FROM research_workers LIMIT 0"))
                checks["schema"] = conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == SCHEMA_REVISION
        except Exception:
            logger.warning("readiness_dependency_unavailable", extra={"dependency": "database"})
        try:
            checks["redis"] = bool(cache.ping())
        except Exception:
            logger.warning("readiness_dependency_unavailable", extra={"dependency": "redis"})
        ok = all(checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={
            "status": "ready" if ok else "not_ready", "checks": checks,
            "market_data": market_health(cache), "trading_enabled": False,
            "mode": config.trading_mode,
        })

    @app.get("/api/v1/system/status")
    def status():
        return {"phase": 5, "mode": config.trading_mode, "live_trading": False,
                "market_data": market_health(cache), "execution": "not_implemented",
                "strategy": "not_implemented", "risk": "not_implemented"}

    return app
