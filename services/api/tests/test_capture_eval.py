"""The capture benchmark: synthetic captures hold only what a camera saw, and scoring is frame-independent."""
import json

import numpy as np
import pytest

from eval.capture_generate import BOX, CAMERA_HEIGHT, Room, cameras, capture, visible
from eval.capture_run import collect, furniture_metrics, load_case, score
from roomshift_api.capture_plan import CABINET, WALL
from roomshift_api.config import REPO_ROOT

import sys
sys.path.insert(0, str(REPO_ROOT / 'services/reconstruction'))
from geometry import write_glb  # noqa: E402


def turning(at, count=24):
    return cameras(count, (at[0], CAMERA_HEIGHT, at[1]))


def test_furniture_hides_the_wall_behind_it():
    room = Room(BOX).box('cabinet', (2.0, 3.7), (1.8, 0.6), 2.2)
    positions, triangles, classes, _, occluders = room.surfaces()
    centres = positions[triangles].mean(axis=1)
    seen = visible(centres, turning((3.0, 1.6)), occluders).any(axis=0)
    behind = (classes == WALL) & (np.abs(centres[:, 2] - 4) < 1e-6) & (np.abs(centres[:, 0] - 2.0) < 0.6) & (centres[:, 1] < 2.0)
    beside = (classes == WALL) & (np.abs(centres[:, 2] - 4) < 1e-6) & (centres[:, 0] > 3.5) & (centres[:, 1] > 1.0) & (centres[:, 1] < 2.0)
    assert behind.sum() > 100 and not seen[behind].any() and seen[beside].all()
    # The wardrobe's front is seen; its back, against the wall, is not.
    cabinet = classes == CABINET
    assert seen[cabinet & (np.abs(centres[:, 2] - 3.4) < 1e-6)].all() and not seen[cabinet & (np.abs(centres[:, 2] - 4.0) < 1e-6)].any()


def test_fully_seen_room_scores_well_whatever_frame_it_arrives_in():
    room = Room(BOX).door(0, 1.45).window(1, 1.9, 1.4).box('table', (1.3, 2.9), (1.2, 0.8), 0.75)
    row = score(capture('box', room, turning((2.8, 1.5)), 0.08))
    assert not row['failed'] and row['layout_iou'] > 0.97 and row['corner_f1'] == 1
    assert row['door_f1'] == row['window_f1'] == row['furniture_f1'] == 1 and row['footprint_iou'] > 0.8
    # Shape is judged at the true scale; the 8% error of the scale hint is reported on its own.
    assert row['dim_rel_err'] < 0.02 and 0.01 < row['scale_err'] < 0.08


def test_furniture_metrics_match_by_category_and_place():
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    shifted = [[x + 0.2, z] for x, z in square]
    assert furniture_metrics([{'category': 'bed', 'polygon': shifted}], [{'category': 'bed', 'polygon': square}]) == {
        'furniture_precision': 1, 'furniture_recall': 1, 'furniture_f1': 1, 'footprint_iou': pytest.approx(0.8 / 1.2)}
    assert furniture_metrics([{'category': 'sofa', 'polygon': square}], [{'category': 'bed', 'polygon': square}])['furniture_f1'] == 0


def test_measured_case_is_collected_and_fitted_to_the_plan_frame(tmp_path):
    """A real case has no known frame: truth measured from any corner, in any orientation, still scores."""
    case = capture('room', Room(BOX).door(0, 1.45), turning((2.8, 1.5)), None, tilt=1.0)
    source = tmp_path / 'reconstruction'
    source.mkdir()
    write_glb(source / 'mesh.glb', case.positions, case.triangles, np.full_like(case.positions, .5),
              np.tile([0, 1., 0], (len(case.positions), 1)))
    (source / 'manifest.json').write_text(json.dumps({
        'units': 'uncalibrated', 'estimatedMetersPerUnit': 1.0, 'cameras': [{'cameraToWorld': pose.tolist()} for pose in case.cameras]}))
    np.savez_compressed(source / 'labels.npz', points=case.labels[0].astype(np.float32), classes=case.labels[1], views=case.labels[2])
    collect(source, tmp_path / 'cases/room')
    # Measured with the long side along z and the origin at another corner.
    (tmp_path / 'cases/room/truth.json').write_text(json.dumps({
        'rooms': [[[10, 20], [14, 20], [14, 25], [10, 25]]], 'openings': [{'type': 'door', 'centre': [10, 21.45], 'width': 0.9}]}))
    row = score(load_case(tmp_path / 'cases/room'))
    assert not row['failed'] and row['layout_iou'] > 0.9 and row['door_f1'] == 1 and row['scale_err'] < 0.06
