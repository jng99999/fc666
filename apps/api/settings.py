from typing import Literal
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: SecretStr
    redis_url: str = "redis://127.0.0.1:6379/0"
    trading_mode: Literal["BACKTEST", "REPLAY", "PAPER"] = "PAPER"
    live_trading: bool = False

    @model_validator(mode="after")
    def reject_live(self):
        if self.live_trading:
            raise ValueError("Live execution is not implemented; LIVE_TRADING must be false")
        return self
