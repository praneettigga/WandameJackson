import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from roomshift_api.config import REPO_ROOT, Settings
from roomshift_api.main import create_app

CONTRACTS = REPO_ROOT / "contracts"
SCHEMA = CONTRACTS / "scene.schema.json"


def make_settings(tmp_path: Path, **over) -> Settings:
    base = dict(
        data_dir=tmp_path / "data", contracts_dir=CONTRACTS, max_upload_bytes=5 * 1024 * 1024,
        max_image_side=8000, dev_seed_fixture=False, cors_origins=("http://localhost:5173",),
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        yield c


def l_shaped_plan() -> np.ndarray:
    """Clean generated L-shaped plan, 0.025 m/px (40 px = 1 m), 8 px (0.2 m) walls.

    Outer centerline: (100,100)-(500,100)-(500,300)-(300,300)-(300,500)-(100,500).
    Interior wall x=300 from y=100..300 splits it into two rooms; a 0.9 m door gap in the bottom wall.
    """
    img = np.full((620, 640), 255, np.uint8)
    t = 4
    def h(y, x0, x1): cv2.rectangle(img, (x0 - t, y - t), (x1 + t - 1, y + t - 1), 0, -1)
    def v(x, y0, y1): cv2.rectangle(img, (x - t, y0 - t), (x + t - 1, y1 + t - 1), 0, -1)
    h(100, 100, 500); v(500, 100, 300); h(300, 300, 500); v(300, 100, 500); v(100, 100, 500)
    h(500, 100, 160); h(500, 196, 300)  # door gap: 164..192 px wide region -> 36 px between thick parts
    cv2.putText(img, "LIVING", (140, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 0, 1)
    return img


def png_bytes(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def wait_job(client, job_id, timeout=20.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()["job"]
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")
