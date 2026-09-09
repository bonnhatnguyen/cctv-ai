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

    model_config = SettingsConfigDict(env_prefix="V1_TRACKING_", extra="ignore")


@lru_cache
def get_settings() -> V1Settings:
    return V1Settings()
