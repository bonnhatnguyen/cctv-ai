"""Environment-backed runtime settings; secrets are never serialized or logged."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./transaction-recognition.db"
    evidence_dir: str = "./data/evidence"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_prefix="TRANSACTION_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
