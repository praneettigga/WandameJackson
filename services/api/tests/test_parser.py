import cv2
import numpy as np
import pytest
from shapely.geometry import Polygon

from roomshift_api.images import load_gray
from roomshift_api.parser import ParseError, parse_blueprint

from conftest import CONTRACTS, l_shaped_plan

MPP = 0.025


def test_fixture_drawing_reconstructs_4x3_room():
    r = parse_blueprint(load_gray(CONTRACTS / "fixtures" / "room.png"), 0.02)
    assert len(r["walls"]) == 4 and len(r["rooms"]) == 1
    poly = Polygon(r["rooms"][0]["polygon"])
    minx, minz, maxx, maxz = poly.bounds
    assert (round(minx, 3), round(minz, 3), round(maxx, 3), round(maxz, 3)) == (1.0, 1.0, 5.0, 4.0)
    kinds = sorted(o["type"] for o in r["openings"])
    assert kinds == ["door", "window"]


def test_generated_l_plan_parsed_by_real_fallback():
    r = parse_blueprint(l_shaped_plan(), MPP)
    walls = r["walls"]
    # 6 distinct centerlines; the shared x=300 wall appears once
    assert len(walls) == 6
    assert sum(1 for w in walls if w["start"][0] == w["end"][0] == 7.5) == 1
    for w in walls:
        assert abs(w["thickness"] - 0.2) < 0.03
        assert w["provenance"]["confidence"] is None
        assert w["provenance"]["fieldOrigins"]["height"] == "inferred"
    areas = sorted(round(Polygon(rm["polygon"]).area, 2) for rm in r["rooms"])
    # left room 5x10 m centerline, right room 5x5 m
    assert areas == [25.0, 50.0]
    doors = [o for o in r["openings"] if o["type"] == "door"]
    assert len(doors) == 1 and doors[0]["bottom"] == 0 and 0.6 <= doors[0]["width"] <= 0.8
    assert r["objects"] == []
    assert any("height" in w.lower() for w in r["warnings"])


def test_blank_image_fails_without_fake_geometry():
    with pytest.raises(ParseError):
        parse_blueprint(np.full((300, 300), 255, np.uint8), 0.02)


def test_noise_fails():
    rng = np.random.default_rng(0)
    with pytest.raises(ParseError):
        parse_blueprint((rng.random((300, 300)) * 255).astype(np.uint8), 0.02)


def test_only_diagonal_lines_fail():
    img = np.full((400, 400), 255, np.uint8)
    cv2.line(img, (20, 20), (380, 380), 0, 8)
    cv2.line(img, (20, 380), (380, 20), 0, 8)
    with pytest.raises(ParseError):
        parse_blueprint(img, 0.02)


def test_tiny_scale_fails_before_allocating_a_huge_kernel():
    with pytest.raises(ParseError, match="calibration"):
        parse_blueprint(l_shaped_plan(), 1e-300)


def test_arbitrary_wall_height_keeps_openings_inside_wall():
    result = parse_blueprint(load_gray(CONTRACTS / "fixtures" / "room.png"), 0.02, wall_height=1.123456)
    assert result["openings"]
    assert all(o["bottom"] + o["height"] <= 1.123456 + 1e-6 for o in result["openings"])
    assert any("Ceilings are inferred" in warning for warning in result["warnings"])
