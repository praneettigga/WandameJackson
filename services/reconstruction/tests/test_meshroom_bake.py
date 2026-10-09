"""CPU checks for the Meshroom output conversion; no Meshroom binary needed."""
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip('trimesh')
PIL = pytest.importorskip('PIL.Image')
from meshroom_worker import export_mesh, read_cameras  # noqa: E402


def textured_quad(folder):
    """Two triangles, left half of the texture red, right half blue (Meshroom's OBJ/MTL layout)."""
    image = np.zeros((64, 64, 3), np.uint8)
    image[:, :32] = [255, 0, 0]
    image[:, 32:] = [0, 0, 255]
    PIL.fromarray(image).save(folder / 'texture_1001.png')
    (folder / 'texturedMesh.mtl').write_text('newmtl TextureAtlas_1001\nmap_Kd texture_1001.png\n')
    (folder / 'texturedMesh.obj').write_text(
        'mtllib texturedMesh.mtl\n'
        'v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n'
        'vt .1 .5\nvt .9 .5\nvt .9 .5\nvt .1 .5\n'
        'usemtl TextureAtlas_1001\nf 1/1 2/2 3/3\nf 1/1 3/3 4/4\n')


def test_textured_mesh_becomes_vertex_colored_glb(tmp_path):
    from geometry import WORLD_TO_VIEWER
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'services/api'))
    from roomshift_api.mesh_worker import validate_glb
    textured_quad(tmp_path)
    # validate_glb needs ≥100 triangles; tile the quad.
    obj = (tmp_path / 'texturedMesh.obj').read_text().split('usemtl')[0]
    faces = ''.join(f'f {1+4*i}/1 {2+4*i}/2 {3+4*i}/3\nf {1+4*i}/1 {3+4*i}/3 {4+4*i}/4\n' for i in range(60))
    vertices = ''.join(f'v {i} 0 0\nv {i+1} 0 0\nv {i+1} 1 0\nv {i} 1 0\n' for i in range(60))
    (tmp_path / 'texturedMesh.obj').write_text(obj.split('v ')[0] + vertices + 'vt .1 .5\nvt .9 .5\nvt .9 .5\nvt .1 .5\nusemtl TextureAtlas_1001\n' + faces)
    out = tmp_path / 'out'
    out.mkdir()
    stats = export_mesh(tmp_path, out)
    assert stats['triangles'] == 120
    assert validate_glb(out / 'mesh.glb')['triangles'] == 120
    assert (out / 'diagnostic.ply').is_file()
    assert WORLD_TO_VIEWER[1, 1] == -1


def test_cameras_sfm_to_viewer_cameras(tmp_path):
    sfm = tmp_path / 'StructureFromMotion/abc'
    sfm.mkdir(parents=True)
    (sfm / 'cameras.sfm').write_text(json.dumps({
        'views': [{'viewId': '1', 'poseId': '1', 'intrinsicId': '9', 'path': '/x/frame_0000.png', 'width': '640', 'height': '480'},
                  {'viewId': '2', 'poseId': '2', 'intrinsicId': '9', 'path': '/x/frame_0001.png', 'width': '640', 'height': '480'}],
        'intrinsics': [{'intrinsicId': '9', 'focalLength': '36', 'sensorWidth': '36', 'principalPoint': ['0', '0']}],
        'poses': [{'poseId': '1', 'pose': {'transform': {'rotation': [str(v) for v in np.eye(3).ravel()], 'center': ['1', '2', '3']}}}]}))
    frames = [{'id': 'frame_0000'}, {'id': 'frame_0001'}, {'id': 'frame_0002'}]
    cameras = read_cameras(tmp_path, frames)
    assert [c['registered'] for c in cameras] == [True, False, False]
    c2w = np.asarray(cameras[0]['cameraToWorld'])
    assert np.allclose(c2w[:3, 3], [1, -2, -3])  # OpenCV world → viewer (Y up, Z back)
    assert np.allclose(np.asarray(cameras[0]['worldToCamera']) @ c2w, np.eye(4))
    assert cameras[0]['intrinsics'][0] == [640, 0, 320]
