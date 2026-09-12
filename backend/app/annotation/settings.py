from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_v1_data_dir(data_dir: Path) -> Path:
    candidate = data_dir if data_dir.is_absolute() else PROJECT_ROOT / data_dir
    return candidate.resolve()


def resolve_annotation_root(data_dir: Path, override: Path | None) -> Path:
    source_root = resolve_v1_data_dir(data_dir)
    if override is None:
        return (source_root / "annotations").resolve()
    if not override.is_absolute():
        raise ValueError("V2 annotation data directory override must be absolute")
    return override.resolve()


class AnnotationSettings(BaseSettings):
    root: Path | None = None
    prepared_limit_bytes: int = 20 * 1024**3
    free_disk_reserve_bytes: int = 2 * 1024**3
    frame_cache_limit_bytes: int = 512 * 1024**2
    frame_queue_limit: int = 32
    process_stall_timeout_seconds: float = 120
    preparation_deadline_seconds: float = 3600
    frame_request_deadline_seconds: float = 10
    assistance_enabled: bool = True
    assistance_python: Path | None = PROJECT_ROOT / ".venv-assist-benchmark" / "Scripts" / "python.exe"
    assistance_model_root: Path | None = PROJECT_ROOT / "data" / "v1" / "assisted-models"
    assistance_deadline_seconds: float = 900
    assistance_poll_seconds: float = 0.25
    assistance_output_limit_bytes: int = 512 * 1024**2
    assistance_max_frames: int = 1800
    assistance_stride: int = 5
    assistance_queue_limit: int = 4
    assistance_dino_device: str = "cuda:0"

    model_config = SettingsConfigDict(env_prefix="V2_ANNOTATION_", extra="ignore")

    @field_validator("root")
    @classmethod
    def require_absolute_override(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("annotation root must be absolute")
        return value.resolve() if value is not None else None

    @field_validator("assistance_python", "assistance_model_root")
    @classmethod
    def require_absolute_assistance_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("assistance paths must be absolute")
        return value.resolve() if value is not None else None

    @classmethod
    def for_data_dir(cls, data_dir: Path) -> "AnnotationSettings":
        configured = cls()
        return configured.model_copy(
            update={"root": resolve_annotation_root(data_dir, configured.root)}
        )
