import cv2
import numpy as np
import pytest
from shapely.geometry import Polygon

from roomshift_api.images import load_gray
from roomshift_api.parser import ParseError, ParserOptions, parse_blueprint, skew_angle

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
        assert 0 <= w["provenance"]["confidence"] <= 1
        assert {f["label"] for f in w["provenance"]["confidenceFactors"]} == {"Stroke coverage", "Width consistency", "Junctions"}
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


def test_only_diagonal_lines_give_walls_but_no_rooms():
    img = np.full((400, 400), 255, np.uint8)
    cv2.line(img, (20, 20), (380, 380), 0, 8)
    cv2.line(img, (20, 380), (380, 20), 0, 8)
    r = parse_blueprint(img, 0.02)
    assert len(r["walls"]) == 2 and r["rooms"] == []
    assert all(type(v) is float for w in r["walls"] for v in w["start"] + w["end"])
    with pytest.raises(ParseError):
        parse_blueprint(img, 0.02, options=ParserOptions(diagonal_walls=False))


def test_tiny_scale_fails_before_allocating_a_huge_kernel():
    with pytest.raises(ParseError, match="calibration"):
        parse_blueprint(l_shaped_plan(), 1e-300)


def test_arbitrary_wall_height_keeps_openings_inside_wall():
    result = parse_blueprint(load_gray(CONTRACTS / "fixtures" / "room.png"), 0.02, wall_height=1.123456)
    assert result["openings"]
    assert all(o["bottom"] + o["height"] <= 1.123456 + 1e-6 for o in result["openings"])
    assert any("Ceilings are inferred" in warning for warning in result["warnings"])


def test_confidence_scores_explain_detected_geometry():
    r = parse_blueprint(load_gray(CONTRACTS / "fixtures" / "room.png"), 0.02)
    for entity in r["walls"] + r["openings"] + r["rooms"]:
        prov = entity["provenance"]
        assert prov["confidenceFactors"], entity["id"]
        assert all(0 <= f["score"] <= 1 and f["detail"] for f in prov["confidenceFactors"])
        scores = [f["score"] for f in prov["confidenceFactors"]]
        assert min(scores) - 0.01 <= prov["confidence"] <= max(scores) + 0.01
    assert all(w["provenance"]["confidence"] >= 0.9 for w in r["walls"])
    door = next(o for o in r["openings"] if o["type"] == "door")
    symbol = next(f for f in door["provenance"]["confidenceFactors"] if f["label"] == "Door symbol")
    assert symbol["score"] < 1 and door["provenance"]["confidence"] < 0.9


def test_free_standing_wall_scores_lower_than_joined_walls():
    img = np.full((400, 400), 255, np.uint8)
    cv2.rectangle(img, (50, 50), (350, 350), 0, 8)
    cv2.line(img, (150, 200), (250, 200), 0, 8)  # isolated partition stroke inside the room
    r = parse_blueprint(img, MPP)
    scores = {tuple(w["start"] + w["end"]): w["provenance"]["confidence"] for w in r["walls"]}
    lone = min(scores.values())
    assert lone < 0.8 and max(scores.values()) >= 0.95


def _rotate_plan(img: np.ndarray, deg: float) -> np.ndarray:
    h, w = img.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(img, M, (w, h), borderValue=255)


def test_deskew_recovers_a_rotated_plan():
    plan = np.pad(l_shaped_plan(), 80, constant_values=255)
    rotated = _rotate_plan(plan, 4.0)
    assert skew_angle(rotated) is not None and abs(abs(skew_angle(rotated)) - 4.0) < 0.5
    straight = parse_blueprint(rotated, MPP)
    areas = sorted(round(Polygon(rm["polygon"]).area) for rm in straight["rooms"])
    assert areas == [25, 50]
    assert any("rotated" in w for w in straight["warnings"])
    # Without deskew, rectilinear extraction fails or loses rooms on the same image.
    try:
        raw = parse_blueprint(rotated, MPP, options=ParserOptions(deskew=False))
        assert sorted(round(Polygon(rm["polygon"]).area) for rm in raw["rooms"]) != [25, 50]
    except ParseError:
        pass


def test_outline_walls_are_filled():
    img = np.full((400, 500), 255, np.uint8)
    # Double-line walls: two 2 px strokes 10 px (0.2 m) apart around a 8 x 6 m room.
    for d in (0, 10):
        cv2.rectangle(img, (50 + d, 50 + d), (450 - d, 350 - d), 0, 2)
    r = parse_blueprint(img, 0.02)
    assert len(r["walls"]) == 4 and len(r["rooms"]) == 1
    assert any("double outlines" in w for w in r["warnings"])
    # Without the stage, each outline stroke becomes its own thin wall.
    assert len(parse_blueprint(img, 0.02, options=ParserOptions(outline_walls=False))["walls"]) == 8


def test_ablation_options_cover_every_stage():
    names = set(ParserOptions.ablations())
    assert names == {"full", "no_deskew", "no_outline_walls", "no_diagonal_walls", "no_endpoint_snap",
                     "no_opening_detection", "no_thin_line_removal", "no_pier_split", "no_soft_gap_ink"}
    r = parse_blueprint(load_gray(CONTRACTS / "fixtures" / "room.png"), 0.02,
                        options=ParserOptions(opening_detection=False))
    assert r["openings"] == []


def test_door_swing_side_and_hinge_match_the_drawn_symbol():
    import math
    from eval.generate import generate
    checked = 0
    for seed in range(6):
        plan = generate(seed, clutter=False)
        r = parse_blueprint(plan.image, plan.mpp)
        walls = {w["id"]: w for w in r["walls"]}
        for o in r["openings"]:
            note = next((n for n in o["provenance"]["notes"] if n.startswith("Swing:")), None)
            if o["type"] != "door" or note is None:
                continue
            w = walls[o["wallId"]]
            L = math.dist(w["start"], w["end"])
            f = (o["offset"] + o["width"] / 2) / L
            c = [w["start"][0] + (w["end"][0] - w["start"][0]) * f, w["start"][1] + (w["end"][1] - w["start"][1]) * f]
            gt = min((g for g in plan.openings if g["type"] == "door"), key=lambda g: math.dist(g["centre"], c))
            if math.dist(gt["centre"], c) > 0.15:
                continue
            horizontal = w["start"][1] == w["end"][1]
            # Parser walls run +x / +z; 'left' is (−dz, dx): +z for horizontal walls, −x for vertical ones.
            left = "left side" in note
            world = (1 if left else -1) if horizontal else (-1 if left else 1)
            assert world == gt["swing_side"], (seed, note, gt)
            assert "near edge" in note  # generator hinges are at the smaller coordinate
            checked += 1
    assert checked >= 5


def test_completeness_warnings_flag_dangling_walls_and_doorless_rooms():
    from roomshift_api.parser import completeness_warnings
    walls = [{"id": "a", "start": [0, 0], "end": [4, 0], "thickness": 0.2},
             {"id": "b", "start": [4, 0], "end": [4, 3], "thickness": 0.2}]
    rooms = [{"id": "room-1", "polygon": [[0, 0], [4, 0], [4, 3], [0, 3]]}]
    out = completeness_warnings(walls, [], rooms)
    assert any("2 wall end(s)" in w for w in out) and any("room-1 has no detected door" in w for w in out)
    door = {"type": "door", "wallId": "a", "offset": 1, "width": 0.9}
    assert not any("no detected door" in w for w in completeness_warnings(walls, [door], rooms))
