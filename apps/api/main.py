import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import create_engine, text
from core.storage.schema import SCHEMA_REVISION
from apps.api.settings import Settings
from apps.api.research import router_for as research_router
from apps.api.replay import router_for as replay_router
from apps.api.paper import router_for as paper_router
from apps.api.requested_paper import router_for as requested_paper_router
from apps.api.portfolio import router_for as portfolio_router
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
    app.include_router(replay_router(engine))
    app.include_router(paper_router(engine))
    app.include_router(requested_paper_router(engine,config))
    app.include_router(portfolio_router(engine))

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
                conn.execute(text("SELECT session_id FROM replay_sessions LIMIT 0"))
                conn.execute(text("SELECT session_id FROM paper_sessions LIMIT 0"))
                conn.execute(text("SELECT session_id FROM paper_streams LIMIT 0"))
                conn.execute(text("SELECT worker_id FROM paper_workers LIMIT 0"))
                conn.execute(text("SELECT event_id FROM paper_controls LIMIT 0"))
                conn.execute(text("SELECT scenario_id FROM portfolio_scenarios LIMIT 0"))
                conn.execute(text("SELECT snapshot_id FROM portfolio_snapshots LIMIT 0"))
                conn.execute(text("SELECT preparation_id FROM paper_funding_reservations LIMIT 0"))
                conn.execute(text("SELECT preparation_id FROM paper_funding_outcomes LIMIT 0"))
                conn.execute(text("SELECT account_id FROM requested_paper_accounts LIMIT 0"))
                conn.execute(text("SELECT request_id FROM requested_paper_requests LIMIT 0"))
                conn.execute(text("SELECT request_id FROM requested_paper_events LIMIT 0"))
                conn.execute(text("SELECT account_id FROM requested_paper_policies LIMIT 0"))
                conn.execute(text("SELECT account_id FROM requested_paper_controls LIMIT 0"))
                conn.execute(text("SELECT request_id FROM requested_paper_gates LIMIT 0"))
                conn.execute(text("SELECT request_id FROM requested_paper_voids LIMIT 0"))
                conn.execute(text("SELECT source_version,ownership_token,ownership_accepted_us,dispatch_version FROM requested_paper_events LIMIT 0"))
                conn.execute(text("SELECT request_id FROM requested_paper_claims LIMIT 0"))
                conn.execute(text("SELECT request_id FROM requested_paper_dispatches LIMIT 0"))
                conn.execute(text("SELECT request_id FROM requested_paper_attempts LIMIT 0"))
                conn.execute(text("SELECT pool_id FROM paper_capital_pools LIMIT 0"))
                conn.execute(text("SELECT account_id FROM paper_capital_members LIMIT 0"))
                conn.execute(text("SELECT request_id FROM requested_paper_sources LIMIT 0"))
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
        return {"phase": 8, "mode": config.trading_mode, "live_trading": False,
                "market_data": market_health(cache), "execution": "not_implemented",
                "strategy": "builtin_ema_sma_v1", "risk": "historical_paper_entry_limits_v1",
                "paper": "historical_next_open_and_realtime_closed_close_v1"}

    return app
