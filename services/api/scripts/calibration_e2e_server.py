"""Isolated browser-test API with synthetic geometry; never a product fallback."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'services/api'), str(ROOT/'services/reconstruction')]
import numpy as np
import uvicorn
from geometry import write_glb
from roomshift_api.config import Settings
from roomshift_api.main import create_app
from roomshift_api.storage import atomic_write_json


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='roomshift-calibration-e2e-') as directory:
        settings = replace(Settings.from_env(), data_dir=Path(directory), dev_seed_fixture=False,
                           cors_origins=('http://127.0.0.1:5189',))
        app = create_app(settings)
        storage = app.state.storage
        root = storage.project_dir('p_calibration')
        output = root/'reconstructions/j_mesh'
        output.mkdir(parents=True)
        vertices = np.array([[x/5-1, y/5-1, 0] for y in range(11) for x in range(11)])
        triangles = []
        for y in range(10):
            for x in range(10):
                a = y*11+x
                triangles.extend([[a, a+1, a+11], [a+1, a+12, a+11]])
        write_glb(output/'mesh.glb', vertices, triangles, np.tile([.7, .8, .4], (len(vertices), 1)), np.tile([0, 0, 1], (len(vertices), 1)))
        atomic_write_json(output/'manifest.json', {'schemaVersion': '1.0.0', 'kind': 'room-mesh',
            'projectId': 'p_calibration', 'jobId': 'j_mesh', 'inputJobId': 'j_input', 'units': 'uncalibrated',
            'calibration': None, 'warnings': ['Synthetic browser test plane.'],
            'cameras': [{'frameId': 'f0', 'cameraToWorld': np.eye(4).tolist(), 'worldToCamera': np.eye(4).tolist(), 'intrinsics': np.eye(3).tolist()}],
            'statistics': {'vertices': len(vertices), 'triangles': len(triangles), 'executionSeconds': 1, 'endToEndSeconds': 1}})
        storage.save_project({'id': 'p_calibration', 'name': 'Calibration browser test', 'createdAt': '2026-10-09T00:00:00Z',
            'hasScene': False, 'source': {'kind': 'photo-set'}, 'hasMesh': True,
            'meshManifestPath': 'reconstructions/j_mesh/manifest.json', 'meshManifestUrl': '/api/projects/p_calibration/mesh'})
        # Independent fixtures for the unified editor's real persistence/asset tests.
        from bake_architectural_demo import build
        from meshroom_worker import vertex_normals
        for pid in ('p_editor_scan', 'p_editor_classroom'):
            output = storage.project_dir(pid)/'reconstructions/j_editor'
            output.mkdir(parents=True)
            if pid == 'p_editor_classroom':
                v, t, colors = build(json.loads((ROOT/'contracts/fixtures/whatsapp-classroom.scene.json').read_text()))
                processing = {'pipeline': 'precomputed-architectural-demo', 'layout': 'whatsapp-classroom.scene.json'}
            else:
                v, t = vertices[:, [0, 2, 1]], triangles
                colors = np.tile([.7, .8, .4], (len(v), 1))
                processing = {}
            write_glb(output/'mesh.glb', v, t, colors, vertex_normals(v, t))
            atomic_write_json(output/'manifest.json', {
                'schemaVersion': '1.0.0', 'kind': 'room-mesh', 'projectId': pid, 'jobId': 'j_editor',
                'inputJobId': 'j_input', 'units': 'meters', 'processing': processing,
                'calibration': {'floor': {'points': [[0, 0, 0], [1, 0, 0], [0, 0, 1]], 'flipNormal': False},
                                'reference': None, 'rotationDegrees': [0, 0, 0]},
                'warnings': ['Synthetic browser test geometry.'], 'cameras': [],
                'statistics': {'vertices': len(v), 'triangles': len(t), 'executionSeconds': 1, 'endToEndSeconds': 1}})
            storage.save_project({'id': pid, 'name': pid, 'createdAt': '2026-10-09T00:00:00Z',
                'hasScene': False, 'source': {'kind': 'photo-set'}, 'hasMesh': True,
                'meshManifestPath': 'reconstructions/j_editor/manifest.json', 'meshManifestUrl': f'/api/projects/{pid}/mesh'})
        uvicorn.run(app, host='127.0.0.1', port=8012)
