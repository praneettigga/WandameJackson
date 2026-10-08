import copy
import json
import math

from roomshift_api.validation import validate_scene, wall_length

from conftest import CONTRACTS, SCHEMA

FIXTURE = json.loads((CONTRACTS / "fixtures" / "room.scene.json").read_text(encoding="utf-8"))


def test_fixture_validates_against_committed_schema():
    assert validate_scene(FIXTURE, SCHEMA) == []
    assert FIXTURE["schemaVersion"] == "0.1.0" and FIXTURE["units"] == "meters"
    assert FIXTURE["source"]["synthetic"] is True


def test_fixture_is_4m_by_3m():
    xs = [p[0] for p in FIXTURE["rooms"][0]["polygon"]]
    zs = [p[1] for p in FIXTURE["rooms"][0]["polygon"]]
    assert math.isclose(max(xs) - min(xs), 4.0) and math.isclose(max(zs) - min(zs), 3.0)
    lengths = sorted(wall_length(w) for w in FIXTURE["walls"])
    assert lengths == [3.0, 3.0, 4.0, 4.0]
    # calibration in the fixture: 100 px = 2 m
    assert math.isclose(FIXTURE["source"]["calibration"]["metersPerPixel"], 0.02)


def _broken(mutate):
    s = copy.deepcopy(FIXTURE)
    mutate(s)
    return validate_scene(s, SCHEMA)


def test_opening_must_fit_wall():
    assert _broken(lambda s: s["openings"][0].update(offset=3.5))  # 3.5 + 0.9 > 4 m wall
    assert _broken(lambda s: s["openings"][1].update(height=2.0))  # 0.9 + 2.0 > 2.7
    assert _broken(lambda s: s["openings"][0].update(bottom=0.1))  # door bottom must be 0
    assert _broken(lambda s: s["openings"][0].update(wallId="nope"))


def test_geometry_rules():
    assert _broken(lambda s: s["walls"][0].update(end=s["walls"][0]["start"]))
    assert _broken(lambda s: s["rooms"][0]["polygon"].append(s["rooms"][0]["polygon"][0]))  # closed polygon
    assert _broken(lambda s: s["objects"].append(copy.deepcopy(s["objects"][0])))  # duplicate id
    assert _broken(lambda s: s["objects"][0].update(dimensions=[1, 0, 1]))
    assert _broken(lambda s: s["objects"][0].update(scale=[1, 1, 1]))  # scale is never persisted
    assert _broken(lambda s: s.update(units="m"))


def test_overlapping_openings_and_nonfinite_dimensions_rejected():
    assert _broken(lambda s: s["openings"].append({**s["openings"][0], "id": "overlap"}))
    for value in (float("nan"), float("inf"), -float("inf")):
        assert _broken(lambda s: s["walls"][0].update(height=value))
        assert _broken(lambda s: s["objects"][0].update(dimensions=[1, value, 1]))


def test_storage_rejects_dot_directory_ids(tmp_path):
    from roomshift_api.storage import Storage
    storage = Storage(tmp_path)
    assert storage.project_dir('.') is None
    assert storage.project_dir('..') is None
    assert storage.project_dir('valid\n') is None
    assert storage.project_dir('demo-room') == tmp_path / 'projects' / 'demo-room'
