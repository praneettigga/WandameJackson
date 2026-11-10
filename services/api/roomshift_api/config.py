"""Runtime configuration, read from environment variables.

API-level settings live on `Settings` (built once per app). Worker settings are read when a job
runs, because the reconstruction subprocess inherits the same environment; their accessors below
validate values and document the variables in one place.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = "0.1.0"
API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent
DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
DEFAULT_HOSTS = ("localhost", "127.0.0.1", "[::1]")
MB = 1024 * 1024


class ConfigError(ValueError):
    """A ROOMSHIFT_* environment variable has an invalid value."""


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _number(name: str, default: float, *, minimum: float, integer: bool = False) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a {'whole ' if integer else ''}number, got {raw!r}.") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {raw!r}.")
    return value


def _list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    return tuple(v.strip() for v in raw.split(",") if v.strip()) if raw else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    contracts_dir: Path
    max_upload_bytes: int
    max_image_side: int
    dev_seed_fixture: bool
    cors_origins: tuple[str, ...] = DEFAULT_ORIGINS
    # Host headers accepted; rejects DNS-rebinding requests aimed at the local API.
    allowed_hosts: tuple[str, ...] = DEFAULT_HOSTS
    max_capture_bytes: int = 512 * MB
    max_json_bytes: int = 32 * MB

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("ROOMSHIFT_DATA_DIR", API_DIR / "data")).resolve(),
            contracts_dir=Path(os.environ.get("ROOMSHIFT_CONTRACTS_DIR", REPO_ROOT / "contracts")).resolve(),
            max_upload_bytes=int(_number("ROOMSHIFT_MAX_UPLOAD_MB", 20, minimum=1, integer=True)) * MB,
            max_image_side=int(_number("ROOMSHIFT_MAX_IMAGE_SIDE", 8000, minimum=64, integer=True)),
            dev_seed_fixture=_flag("ROOMSHIFT_DEV_SEED_FIXTURE"),
            cors_origins=_list("ROOMSHIFT_CORS_ORIGINS", DEFAULT_ORIGINS),
            allowed_hosts=_list("ROOMSHIFT_ALLOWED_HOSTS", DEFAULT_HOSTS),
            max_capture_bytes=int(_number("ROOMSHIFT_MAX_CAPTURE_MB", 512, minimum=1, integer=True)) * MB,
        )


# --- settings read at job time --------------------------------------------------------------------
def ocr_timeout() -> float:
    """ROOMSHIFT_OCR_TIMEOUT: seconds per OCR pass (slow machines fall back to an estimated scale)."""
    return _number("ROOMSHIFT_OCR_TIMEOUT", 20.0, minimum=1)


def reconstruction_engine() -> str:
    """ROOMSHIFT_RECONSTRUCTION_ENGINE: `meshroom` (default) or the optional `vggt` worker."""
    engine = os.environ.get("ROOMSHIFT_RECONSTRUCTION_ENGINE", "meshroom").strip().lower() or "meshroom"
    if engine not in {"meshroom", "vggt"}:
        raise ConfigError(f"ROOMSHIFT_RECONSTRUCTION_ENGINE must be meshroom or vggt, got {engine!r}.")
    return engine


def reconstruction_timeout() -> float:
    """ROOMSHIFT_RECONSTRUCTION_TIMEOUT: seconds for one mesh job, including capture preparation."""
    return _number("ROOMSHIFT_RECONSTRUCTION_TIMEOUT", 3600, minimum=1)


def demo_presets_enabled() -> bool:
    """ROOMSHIFT_DEMO_PRESETS=1 replays offline-baked meshes for known demo videos."""
    return os.environ.get("ROOMSHIFT_DEMO_PRESETS") == "1"
