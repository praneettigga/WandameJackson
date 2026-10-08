import math

from shapely.geometry import Polygon

from eval.generate import AUGMENTATIONS, downscale, generate, skew
from eval.metrics import evaluate, junctions, opening_metrics, room_metrics, wall_iou
from roomshift_api.parser import parse_blueprint


def _gt(plan):
    return {"walls": plan.walls, "openings": plan.openings, "rooms": plan.rooms}


def test_identical_layout_scores_perfectly():
    plan = generate(1)
    walls = [{"id": f"w{i}", **w} for i, w in enumerate(plan.walls)]
    rooms = [{"polygon": r} for r in plan.rooms]
    m = evaluate({"walls": walls, "openings": [], "rooms": rooms}, _gt(plan))
    assert math.isclose(m["wall_iou"], 1) and math.isclose(m["layout_iou"], 1)
    assert m["room_f1"] == 1 and m["corner_f1"] == 1 and m["dim_abs_err_m"] < 1e-9


def test_shifted_wall_and_room_have_known_errors():
    gt = [{"start": [0, 0], "end": [4, 0], "thickness": 0.2}]
    shifted = [{"start": [0, 0.1], "end": [4, 0.1], "thickness": 0.2}]
    assert math.isclose(wall_iou(shifted, gt), 1 / 3, rel_tol=1e-6)  # overlap 0.1 of 0.3 total
    m = room_metrics([[[0, 0], [4, 0], [4, 3.3], [0, 3.3]]], [[[0, 0], [4, 0], [4, 3], [0, 3]]])
    assert m["room_f1"] == 1 and math.isclose(m["dim_abs_err_m"], 0.15)
    assert room_metrics([[[0, 0], [1, 0], [1, 1], [0, 1]]], [[[0, 0], [4, 0], [4, 3], [0, 3]]])["room_f1"] == 0


def test_opening_matching_needs_type_and_position():
    gt = [{"type": "door", "centre": [1, 0], "width": 0.9}]
    assert opening_metrics([{"type": "door", "centre": [1.1, 0], "width": 0.8}], gt)["door_f1"] == 1
    assert opening_metrics([{"type": "window", "centre": [1, 0], "width": 0.9}], gt)["door_f1"] == 0
    assert opening_metrics([{"type": "door", "centre": [1.3, 0], "width": 0.9}], gt)["door_f1"] == 0


def test_generator_ground_truth_is_consistent_and_augmentations_keep_it_exact():
    plan = generate(3)
    total = sum(Polygon(r).area for r in plan.rooms)
    assert math.isclose(total, plan.meta["size"][0] * plan.meta["size"][1], rel_tol=1e-6)
    assert len(junctions(plan.walls)) >= 4
    small = downscale(plan, 0.5)
    assert math.isclose(small.mpp, plan.mpp * 2)
    assert math.isclose(sum(Polygon(r).area for r in small.rooms), total, rel_tol=1e-6)
    turned = skew(plan, 3)
    assert math.isclose(sum(Polygon(r).area for r in turned.rooms), total, rel_tol=1e-6)
    assert set(AUGMENTATIONS) >= {"clean", "skew3", "half_res"}


def test_parser_recovers_a_clean_synthetic_plan():
    plan = generate(0)
    m = evaluate(parse_blueprint(plan.image, plan.mpp), _gt(plan))
    assert m["layout_iou"] > 0.95 and m["room_f1"] == 1 and m["wall_iou"] > 0.85


def test_fusion_accepts_only_ink_backed_missing_walls():
    from eval.fusion import fuse_walls
    plan = generate(2)
    ours = parse_blueprint(plan.image, plan.mpp)
    victim = ours["walls"][0]
    reduced = {**ours, "walls": ours["walls"][1:]}
    bogus = {"start": [0.2, 0.2], "end": [0.2, 3.0], "thickness": 0.2}  # in the blank margin
    proposed = {"walls": [{"start": victim["start"], "end": victim["end"], "thickness": victim["thickness"]}, bogus]}
    fused = fuse_walls(reduced, proposed, plan.image, plan.mpp, source="test-proposer")
    added = [w for w in fused["walls"] if w["id"].startswith("wall-fused")]
    assert len(added) == 1 and added[0]["start"] == victim["start"]
    assert added[0]["provenance"]["source"] == "test-proposer"
    # Walls we already have are never duplicated.
    assert len(fuse_walls(ours, proposed, plan.image, plan.mpp)["walls"]) == len(ours["walls"])


def test_outline_and_diagonal_styles_have_consistent_ground_truth():
    plain = generate(4)
    chamfered = generate(4, chamfer=True)
    total = lambda p: sum(Polygon(r).area for r in p.rooms)  # noqa: E731
    assert math.isclose(total(plain) - total(chamfered), 1.5 * 1.5 / 2, rel_tol=1e-6)
    assert any(abs(w["start"][0] - w["end"][0]) > 1e-6 and abs(w["start"][1] - w["end"][1]) > 1e-6
               for w in chamfered.walls)
    outline = generate(4, style="outline")
    assert outline.walls == plain.walls and outline.rooms == plain.rooms
    # Outline drawings have much less ink than solid ones.
    assert (outline.image < 128).sum() < 0.8 * (plain.image < 128).sum()


def test_parser_handles_diagonal_and_outline_styles():
    """Regression floor over a fixed seed range (no hand-picked seeds). Outline plans are a known weak
    spot (see docs/eval/RESULTS.md), so their floor is lower."""
    def mean(kw, metric):
        vals = []
        for seed in range(6):
            plan = generate(seed, **kw)
            vals.append(evaluate(parse_blueprint(plan.image, plan.mpp), _gt(plan))[metric])
        return sum(vals) / len(vals)

    assert mean({"chamfer": True}, "layout_iou") > 0.95
    assert mean({"chamfer": True}, "room_f1") > 0.9
    assert mean({"style": "outline"}, "layout_iou") > 0.6
