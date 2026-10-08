"""End-to-end smoke test against a running API.

Usage (API already running):
    python scripts/smoke.py [--base http://127.0.0.1:8000] [--image path.png --a X Y --b X Y --meters D]

Without --image it generates the clean L-shaped test plan (400 px = 10 m).
"""
from __future__ import annotations

import argparse
import copy
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from roomshift_api.config import REPO_ROOT  # noqa: E402
from roomshift_api.validation import validate_scene  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--image")
    ap.add_argument("--a", nargs=2, type=float, default=[100, 100])
    ap.add_argument("--b", nargs=2, type=float, default=[500, 100])
    ap.add_argument("--meters", type=float, default=10.0)
    args = ap.parse_args()

    if args.image:
        data, name = Path(args.image).read_bytes(), Path(args.image).name
    else:
        from conftest import l_shaped_plan, png_bytes
        data, name = png_bytes(l_shaped_plan()), "generated-l-plan.png"

    c = httpx.Client(base_url=args.base, timeout=30)
    print("1. health:", c.get("/api/health").json())

    r = c.post("/api/projects", files={"blueprint": (name, data)})
    r.raise_for_status()
    pid = r.json()["project"]["id"]
    print("2. uploaded:", pid, r.json()["image"])

    r = c.post(f"/api/projects/{pid}/reconstruct",
               json={"calibration": {"pointA": args.a, "pointB": args.b, "distanceMeters": args.meters}})
    r.raise_for_status()
    job = r.json()["job"]
    print("3. job queued:", job["id"])

    while job["status"] not in ("succeeded", "failed"):
        time.sleep(0.25)
        job = c.get(f"/api/jobs/{job['id']}").json()["job"]
    if job["status"] == "failed":
        print("   job FAILED:", job["error"])
        return 1
    scene = c.get(job["sceneUrl"]).json()
    problems = validate_scene(scene, REPO_ROOT / "contracts" / "scene.schema.json")
    print(f"4. scene: walls={len(scene['walls'])} rooms={len(scene['rooms'])} openings={len(scene['openings'])} schema-valid={not problems}")
    if problems:
        print(problems)
        return 1

    edit = copy.deepcopy(scene)
    edit["objects"].append({
        "id": f"obj-{uuid.uuid4()}", "name": "Chair", "category": "chair", "componentId": "chair.basic",
        "position": [scene["walls"][0]["start"][0] + 1, 0, scene["walls"][0]["start"][1] + 1],
        "rotationY": 0.0, "dimensions": [0.5, 0.9, 0.5],
        "provenance": {"origin": "user", "confidence": None, "source": "user", "userEdited": True, "fieldOrigins": {}, "notes": []},
    })
    saved = c.put(f"/api/projects/{pid}/scene", json=edit)
    saved.raise_for_status()
    reloaded = c.get(f"/api/projects/{pid}/scene").json()
    source = c.get(f"/api/projects/{pid}/source-scene").json()
    ok = reloaded["revision"] == 1 and reloaded["objects"] == edit["objects"] and source == scene
    print(f"5. saved rev={saved.json()['revision']} reloaded objects={len(reloaded['objects'])} source unchanged={source == scene}")
    print("SMOKE", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
