"""Environment-backed settings isolated from the legacy live application."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class V1Settings(BaseSettings):
    model_path: Path = Path("backend/weights/yolo26n.pt")
    data_dir: Path = Path("data/v1")
    evidence_dir: Path = Path("data/evidence")
    device: str = "auto"
    image_size: int = 960
    max_upload_bytes: int = 4 * 1024**3
    database_url: str | None = None
    progress_interval_seconds: float = 0.25

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'jobs.db').resolve().as_posix()}"

    model_config = SettingsConfigDict(env_prefix="V1_TRACKING_", extra="ignore")


@lru_cache
def get_settings() -> V1Settings:
    return V1Settings()
