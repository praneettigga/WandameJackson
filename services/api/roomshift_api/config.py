"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = "0.1.0"
API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    contracts_dir: Path
    max_upload_bytes: int
    max_image_side: int
    dev_seed_fixture: bool
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("ROOMSHIFT_DATA_DIR", API_DIR / "data")).resolve(),
            contracts_dir=Path(os.environ.get("ROOMSHIFT_CONTRACTS_DIR", REPO_ROOT / "contracts")).resolve(),
            max_upload_bytes=int(os.environ.get("ROOMSHIFT_MAX_UPLOAD_MB", "20")) * 1024 * 1024,
            max_image_side=int(os.environ.get("ROOMSHIFT_MAX_IMAGE_SIDE", "8000")),
            dev_seed_fixture=_flag("ROOMSHIFT_DEV_SEED_FIXTURE"),
            cors_origins=("http://localhost:5173", "http://127.0.0.1:5173"),
        )
