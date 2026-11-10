"""Dev-only seeding of the committed synthetic fixtures (ROOMSHIFT_DEV_SEED_FIXTURE=1)."""
from __future__ import annotations

import json
from pathlib import Path

from .storage import Storage, now_iso

DEMO_ID = "demo-room"
# Committed synthetic fixtures exposed as ready-made projects: project id → file stem in contracts/fixtures.
SEEDED_FIXTURES = {DEMO_ID: "room", "services-apartment": "services-apartment"}


def seed_fixture(storage: Storage, contracts_dir: Path) -> None:
    """Expose the committed synthetic fixtures (e.g. `demo-room`). Never used as a parse fallback."""
    for project_id, stem in SEEDED_FIXTURES.items():
        if storage.get_project(project_id):
            continue
        scene = json.loads((contracts_dir / "fixtures" / f"{stem}.scene.json").read_text(encoding="utf-8"))
        png = (contracts_dir / "fixtures" / f"{stem}.png").read_bytes()
        storage.save_blueprint(project_id, png, "image/png")
        storage.save_project({
            "id": project_id, "name": scene["name"], "createdAt": now_iso(), "hasScene": True, "synthetic": True,
            "image": {"url": scene["source"]["imageUrl"], "width": scene["source"]["imageWidth"],
                      "height": scene["source"]["imageHeight"], "mimeType": "image/png"},
        })
        storage.save_reconstruction(project_id, scene)
