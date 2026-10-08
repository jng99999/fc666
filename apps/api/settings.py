from typing import Literal
from pydantic import SecretStr, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    database_url: SecretStr
    redis_url: str = "redis://127.0.0.1:6379/0"
    trading_mode: Literal["BACKTEST", "REPLAY", "PAPER"] = "PAPER"
    live_trading: bool = False
    paper_operator_token: SecretStr | None = None
    paper_operator_accounts: list[str] = Field(default_factory=list)
    paper_operator_pool_ids: list[str] = Field(default_factory=list)
    paper_operator_actions: list[Literal["PAUSE", "HALT", "STOP", "RESUME", "VOID_UNSUBMITTED", "ENROLL", "PREVIEW_PREPARE", "PREPARE", "PREVIEW_EVENT", "INGEST_EVENT", "CLAIM_OWNERSHIP", "DELIVER_OWNED_EVENT", "QUERY_DISPATCH", "RECORD_ASSESSMENT", "READ_ASSESSMENTS", "PREVIEW_POOL_PREPARE", "POOL_PREPARE", "READ_POOL", "ENROLL_HEALTH", "READ_HEALTH"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_paper_operator(self):
        import re
        configured = self.paper_operator_token is not None
        if configured != bool(self.paper_operator_accounts) or configured != bool(self.paper_operator_actions):
            raise ValueError("Paper operator requires token, explicit accounts and actions together")
        if configured and not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", self.paper_operator_token.get_secret_value()):
            raise ValueError("Paper operator token must contain 32..256 URL-safe characters")
        if (len(self.paper_operator_accounts)>100 or any(not account or len(account)>128 for account in self.paper_operator_accounts)
            or len(set(self.paper_operator_accounts))!=len(self.paper_operator_accounts)
            or len(set(self.paper_operator_actions))!=len(self.paper_operator_actions)):
            raise ValueError("Paper operator grants must be bounded and unique")
        if (len(self.paper_operator_pool_ids)>100 or any(not value or len(value)>128 for value in self.paper_operator_pool_ids)
            or len(set(self.paper_operator_pool_ids))!=len(self.paper_operator_pool_ids)):
            raise ValueError("Paper pool grants must be bounded and unique")
        pool_actions={'PREVIEW_POOL_PREPARE','POOL_PREPARE','READ_POOL'}
        if self.paper_operator_pool_ids and not configured:
            raise ValueError("Pool scope requires configured Paper operator")
        if pool_actions.intersection(self.paper_operator_actions) and not self.paper_operator_pool_ids:
            raise ValueError("Pool actions require explicit pool IDs")
        return self


    @model_validator(mode="after")
    def reject_live(self):
        if self.live_trading:
            raise ValueError("Live execution is not implemented; LIVE_TRADING must be false")
        return self
