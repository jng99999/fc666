"""Requires real PostgreSQL/TimescaleDB and Redis; missing dependencies fail."""
from decimal import Decimal
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from apps.api.main import create_app
from apps.api.settings import Settings
from core.storage.models import InstrumentRecord, CandleRecord
from core.models import Candle

@pytest.fixture
def database(monkeypatch):
    settings = Settings()
    original = make_url(settings.database_url.get_secret_value())
    admin = create_engine(original, isolation_level="AUTOCOMMIT")
    name = "fc666_test_" + uuid4().hex
    with admin.connect() as conn: conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = original.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL",url)
    config = Config("alembic.ini")
    engine = create_engine(url)
    try:
        command.upgrade(config,"head")
        yield engine, config, Settings()
    finally:
        engine.dispose()
        with admin.connect() as conn:
            # Fence new connections before FORCE terminates existing sessions;
            # extension/background reconnects must not race test-database removal.
            conn.execute(text(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false'))
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()

def instrument():
    return InstrumentRecord(instrument_id="test:spot:BTC-USDT",exchange="test",native_symbol="BTCUSDT",base="BTC",quote="USDT",
        market_type="SPOT",tick_size=Decimal(".01"),quantity_step=Decimal(".001"),min_quantity=Decimal(".001"),min_notional=Decimal("5"),contract_size=Decimal("1"))

def test_migration_roundtrip_and_hypertable(database):
    engine, config, _ = database
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name='candles'")).scalar() == 1
    command.downgrade(config,"base")
    with engine.connect() as conn: assert conn.execute(text("SELECT to_regclass('public.instruments')")).scalar() is None
    command.upgrade(config,"head")
    command.upgrade(config,"head")
    command.check(config)

def test_decimal_storage_and_duplicate_candle(database):
    engine,_,_ = database
    bar = Candle(instrument_id="test:spot:BTC-USDT",timeframe="1m",open_time="2026-01-01T00:00:00Z",close_time="2026-01-01T00:01:00Z",
        open="100.000000000000000001",high="101",low="99",close="100",volume="1.25",is_closed=True,source="synthetic-test")
    with Session(engine) as session:
        session.add(instrument());session.commit()
        session.add(CandleRecord(**bar.model_dump()));session.commit()
        loaded = session.get(CandleRecord,(bar.instrument_id,bar.timeframe,bar.open_time))
        assert loaded.open == Decimal("100.000000000000000001")
    with Session(engine) as session:
        session.add(CandleRecord(**bar.model_dump()))
        with pytest.raises(IntegrityError): session.commit()

def test_db_rule_constraint(database):
    engine,_,_ = database
    with Session(engine) as session:
        bad = instrument();bad.tick_size = Decimal("-1");session.add(bad)
        with pytest.raises(IntegrityError): session.commit()

def test_api_ready_and_no_live_routes(database):
    _,_,settings = database
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status":"alive"}
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["trading_enabled"] is False
        assert response.json()["checks"] == {"database":True,"schema":True,"redis":True}
        assert client.get("/api/v1/system/status").json()["market_data"]["trading_enabled"] is False
        assert client.post("/api/v1/live-orders").status_code == 404

def test_database_down_is_not_ready():
    settings = Settings(database_url="postgresql+psycopg://invalid:invalid@127.0.0.1:1/invalid")
    with TestClient(create_app(settings)) as client:
        response=client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["database"] is False
        assert "invalid:" not in response.text

def test_cache_down_is_not_ready(database):
    _,_,settings = database
    settings.redis_url="redis://127.0.0.1:1/0"
    with TestClient(create_app(settings)) as client:
        response=client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["redis"] is False

def test_unmigrated_schema_is_not_ready(database):
    _,config,settings = database
    command.downgrade(config,"base")
    with TestClient(create_app(settings)) as client:
        response=client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["database"] is True
        assert response.json()["checks"]["schema"] is False
