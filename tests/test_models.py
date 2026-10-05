from datetime import datetime, timezone
from decimal import Decimal
import json
import pytest
from pydantic import ValidationError
from core.models import Candle, Instrument
from apps.api.settings import Settings

@pytest.fixture
def bar():
    return dict(instrument_id="test:spot:BTC-USDT", timeframe="1m", open_time="2026-01-01T00:00:00Z",
        close_time="2026-01-01T00:01:00Z", open="100.000000000000000001", high="101", low="99",
        close="100", volume="1.25", is_closed=True, source="synthetic-test")

def test_decimal_wire_roundtrip(bar):
    candle = Candle(**bar)
    value = json.loads(candle.model_dump_json())["open"]
    assert value == "100.000000000000000001"
    assert Candle.model_validate_json(candle.model_dump_json()) == candle

@pytest.mark.parametrize("change", [{"volume":"-1"}, {"low":"102"}, {"high":"98"},
    {"open_time":"2026-01-01T00:00:00"}, {"close_time":"2025-01-01T00:00:00Z"},
    {"open":"NaN"}, {"open":100.1}, {"volume":True}, {"close_time":"2026-01-01T00:02:00Z"}, {"open_time":"2026-01-01T00:00:00.001Z"}, {"timeframe":"2m"}, {"open":"100.0000000000000000001"}])
def test_invalid_candles_rejected(bar, change):
    with pytest.raises(ValidationError): Candle(**(bar | change))

def test_timezone_normalizes(bar):
    c = Candle(**(bar | {"open_time":"2026-01-01T08:00:00+08:00"}))
    assert c.open_time == datetime(2026,1,1,tzinfo=timezone.utc)

def test_perpetual_requires_contract():
    with pytest.raises(ValidationError):
        Instrument(instrument_id="test:perp:BTC", exchange="test",native_symbol="BTCUSDT",base="BTC",
            quote="USDT",market_type="PERPETUAL",tick_size=Decimal(".01"),quantity_step=Decimal(".001"),min_quantity=0,min_notional=0)

@pytest.mark.parametrize("change", [{"live_trading":True},{"trading_mode":"LIVE"}])
def test_live_cannot_be_enabled(change):
    with pytest.raises(ValidationError):
        Settings(_env_file=None,database_url="postgresql+psycopg://localhost/test",**change)
