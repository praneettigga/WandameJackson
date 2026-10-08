import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest
from geometry import consistent_depths, fuse_mesh, write_glb, WORLD_TO_VIEWER


def plane():
    n, h, w = 3, 80, 96
    k = np.tile([[85., 0, 48], [0, 85., 40], [0, 0, 1]], (n, 1, 1))
    e = np.tile(np.eye(4), (n, 1, 1))
    e[:, 0, 3] = [-.1, 0, .1]
    depth = np.full((n, h, w), 2., dtype=np.float32)
    yy, xx = np.mgrid[:h, :w]
    for i in range(n):
        x, y = (xx-48)*2/85-e[i, 0, 3], (yy-40)*2/85
        depth[i, (abs(x) < .2) & (abs(y) < .2)] = 0
    rgb = np.zeros((n, h, w, 3), dtype=np.uint8)
    rgb[..., 0] = np.linspace(30, 240, w).astype(np.uint8)
    rgb[..., 1] = 160
    return depth, np.ones_like(depth)*4, k, e, np.ones_like(depth, dtype=bool), rgb


def test_fusion_exports_colored_supported_triangles_with_hole(tmp_path):
    depth, confidence, k, e, valid, rgb = plane()
    filtered, evidence = consistent_depths(depth, confidence, k, e, valid, lambda *_: None)
    assert all(n > 1000 for n in evidence['supportedPixels'])
    mesh, stats = fuse_mesh(filtered, rgb, k, e, lambda *_: None)
    assert stats['triangles'] > 100
    positions = np.asarray(mesh.vertices)
    assert np.median(positions[:, 2]) == pytest.approx(-2, abs=.02)
    centers = positions[np.asarray(mesh.triangles)].mean(axis=1)
    assert not ((abs(centers[:, 0]) < .1) & (abs(centers[:, 1]) < .1)).any(), 'Unseen hole was filled'
    assert np.ptp(np.asarray(mesh.vertex_colors)[:, 0]) > .1
    path = tmp_path / 'plane.glb'
    write_glb(path, mesh.vertices, mesh.triangles, mesh.vertex_colors, mesh.vertex_normals)
    assert path.read_bytes()[:4] == b'glTF'
    for camera in e:
        assert np.allclose((camera @ np.linalg.inv(WORLD_TO_VIEWER)) @ (WORLD_TO_VIEWER @ np.linalg.inv(camera)), np.eye(4))


def test_disagreeing_depths_do_not_become_a_mesh():
    depth, confidence, k, e, valid, _ = plane()
    depth = depth[:2].copy()
    depth[1] *= 3
    with pytest.raises(ValueError, match='Too little consistent'):
        consistent_depths(depth, confidence[:2], k[:2], e[:2], valid[:2], lambda *_: None)


def test_single_view_surfaces_kept_only_in_consistent_views():
    depth, confidence, k, e, valid, _ = plane()
    # A confident strip only view 0 sees (e.g. a wall corner) must survive...
    depth[0, :, :8] = 1.2
    confidence[0, :, :8] = 9
    filtered, evidence = consistent_depths(depth, confidence, k, e, valid, lambda *_: None)
    assert (filtered[0, 5:-5, 1:7] > 0).all()
    assert evidence['singleViewPixels'][0] > 0
    # ...but disabling the fallback restores strict multi-view support.
    strict, _ = consistent_depths(depth, confidence, k, e, valid, lambda *_: None, single_view_quantile=1)
    assert not (strict[0, 5:-5, 1:7] > 0).any()


def test_small_floaters_removed_and_triangles_bounded():
    depth, confidence, k, e, valid, rgb = plane()
    filtered, _ = consistent_depths(depth, confidence, k, e, valid, lambda *_: None)
    filtered[0, 2:5, 2:5] = .7  # isolated speck far in front of the wall
    mesh, stats = fuse_mesh(filtered, rgb, k, e, lambda *_: None, max_triangles=2000)
    assert stats['triangles'] <= 2000
    assert np.asarray(mesh.vertices)[:, 2].max() < -1.5


def test_invalid_cameras_and_nonfinite_mesh_rejected(tmp_path):
    depth, confidence, k, e, valid, _ = plane()
    k[0, 0, 0] = np.nan
    with pytest.raises(ValueError, match='Non-finite'):
        consistent_depths(depth, confidence, k, e, valid, lambda *_: None)
    with pytest.raises(ValueError, match='non-finite'):
        write_glb(tmp_path/'bad.glb', [[np.nan,0,0]], [[0,0,0]], [[1,0,0]], [[0,1,0]])


def test_export_rejects_faces_collapsed_by_float32_rounding(tmp_path):
    vertices = np.array([[1., 0, 0], [1.+1e-9, 0, 0], [1., 1., 0]])
    with pytest.raises(ValueError, match='float32 precision'):
        write_glb(tmp_path/'collapsed.glb', vertices, [[0, 1, 2]], np.ones((3, 3)), np.tile([0, 0, 1], (3, 1)))
