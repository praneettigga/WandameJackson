"""Explicit, persistent editor snapshots of a capture. Never infer semantic walls from a mesh."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import struct
import uuid

import cv2
import numpy as np

from .errors import ApiError
from .mesh_calibration import current_mesh
from .storage import atomic_write_bytes, read_json
from .validation import validate_scene


def provenance(generated=False):
    return {'origin': 'generated' if generated else 'inferred', 'confidence': None,
            'source': 'architectural-demo-template' if generated else 'capture-mesh',
            'userEdited': False, 'fieldOrigins': {},
            'notes': ['Precomputed demo layout, not extracted from video.'] if generated else
                     ['Unsegmented reconstructed surface. Individual walls and furniture are not identified.']}


def mesh_positions(path):
    data = path.read_bytes()
    size = struct.unpack_from('<I', data, 12)[0]
    doc = json.loads(data[20:20+size])
    primitive = doc['meshes'][0]['primitives'][0]
    accessor = doc['accessors'][primitive['attributes']['POSITION']]
    view = doc['bufferViews'][accessor['bufferView']]
    return np.frombuffer(data, dtype='<f4', count=accessor['count']*3,
                         offset=28+size+view.get('byteOffset', 0)+accessor.get('byteOffset', 0)).reshape(-1, 3)


def layout_entities(layout, matrix):
    """Retain semantic geometry only when calibration leaves the floor horizontal at y=0."""
    scale = float(np.linalg.norm(matrix[:3, 0]))
    if not np.allclose(matrix[1], [0, scale, 0, 0], atol=1e-6):
        return None
    yaw = math.atan2(matrix[0, 2], matrix[0, 0])
    def point(p):
        return (matrix @ np.array([*p, 1]))[:3].tolist()
    def floor(p):
        q = point([p[0], 0, p[1]])
        return [q[0], q[2]]
    entities = {key: deepcopy(layout.get(key, [])) for key in ('rooms', 'walls', 'openings')}
    for room in entities['rooms']:
        room['polygon'] = [floor(p) for p in room['polygon']]
        room['height'] *= scale
    for wall in entities['walls']:
        wall['start'], wall['end'] = floor(wall['start']), floor(wall['end'])
        wall['height'] *= scale
        wall['thickness'] *= scale
    for opening in entities['openings']:
        for key in ('offset', 'width', 'height', 'bottom'):
            opening[key] *= scale
    for group in entities.values():
        for entity in group:
            entity['provenance'] = provenance(True)
    objects = []
    def box(name, low, high, component=None):
        size = [(high[i]-low[i])*scale for i in range(3)]
        objects.append({'id': f'fixture-{len(objects)+1}', 'name': name, 'category': 'other',
                        'componentId': component, 'position': point([(low[0]+high[0])/2, low[1], (low[2]+high[2])/2]),
                        'rotationY': yaw, 'dimensions': size, 'provenance': provenance(True)})
    for i, fixture in enumerate(layout.get('fixtures', [])):
        box(f"{fixture['kind'].title()} {i+1}", fixture['low'], fixture['high'])
    for rows in layout.get('deskRows', []):
        for row in range(rows['count']):
            for col in range(rows['columns']):
                x = rows['start'][0]+col*(rows['deskWidth']+rows['aisle'])
                z = rows['start'][1]+row*rows['rowSpacing']
                w, d = rows['deskWidth'], rows.get('deskDepth', .48)
                box(f'Desk {row+1}.{col+1}', [x, 0, z], [x+w, rows.get('topY', .78), z+d], 'table.basic')
                box(f'Bench {row+1}.{col+1}', [x, 0, z+d+.16], [x+w, .45, z+d+.46])
    return {**entities, 'objects': objects}


def open_editor_scene(storage, project_id, contracts_dir):
    with storage.project_lock(project_id):
        project = storage.get_project(project_id)
        if project is None:
            raise ApiError(404, 'PROJECT_NOT_FOUND', 'Project not found.')
        if project.get('source', {}).get('kind', 'blueprint') == 'blueprint':
            raise ApiError(400, 'INVALID_SOURCE', 'Open a photo/video capture in the editor.')
        # Reopening never overwrites edits, even after a new reconstruction/calibration.
        existing = storage.get_scene(project_id)
        if existing:
            return existing
        manifest = current_mesh(storage, project)
        uncalibrated = manifest['units'] != 'meters'
        root = storage.project_dir(project_id)
        revision = manifest.get('calibrationRevision')
        mesh_path = (root/'calibrations'/revision/'mesh.glb' if revision else
                     (root/project['meshManifestPath']).with_name('mesh.glb'))
        positions = mesh_positions(mesh_path)
        low, high = positions.min(axis=0), positions.max(axis=0)
        matrix = np.asarray(manifest.get('reconstructionToWorld', np.eye(4)), dtype=float)
        processing = manifest.get('processing', {})
        layout_name = processing.get('layout')
        allowed = {'whatsapp-classroom.scene.json', 'whatsapp-rectangular-room.scene.json'}
        layout = (read_json(contracts_dir/'fixtures'/layout_name)
                  if processing.get('pipeline') == 'precomputed-architectural-demo' and layout_name in allowed else None)
        entities = layout_entities(layout, matrix) if layout else None
        representation = 'layout' if entities else 'mesh'
        unaligned = False
        token = uuid.uuid4().hex
        assets = root/'editor-assets'
        base_url = f'/api/projects/{project_id}/editor-assets'
        if entities is None:
            unaligned = not (manifest.get('calibration') or {}).get('floor') and not layout
            filename = f'{token}.glb'
            atomic_write_bytes(assets/filename, mesh_path.read_bytes())
            entities = {'rooms': [], 'walls': [], 'openings': [], 'objects': [{
                'id': 'capture-mesh', 'name': 'Captured room mesh', 'category': 'scan',
                'componentId': None, 'assetUrl': f'{base_url}/{filename}',
                'position': [float((low[0]+high[0])/2), float(low[1]), float((low[2]+high[2])/2)],
                'rotationY': 0, 'dimensions': np.maximum(high-low, 1e-6).tolist(),
                'provenance': provenance(bool(layout)),
            }]}
        # A real top-down projection for the Scene source image, not a fabricated blueprint.
        span = max(float(high[0]-low[0]), float(high[2]-low[2]), .01)
        meters_per_pixel = span/960
        image = np.full((1024, 1024, 3), 245, dtype=np.uint8)
        pixels = np.rint((positions[::max(1, len(positions)//100000), [0, 2]]-low[[0, 2]])/meters_per_pixel+32).astype(int)
        pixels = np.clip(pixels, 0, 1023)
        image[pixels[:, 1], pixels[:, 0]] = (90, 110, 120)
        ok, png = cv2.imencode('.png', image)
        if not ok:
            raise ApiError(500, 'EDITOR_PREVIEW_FAILED', 'Could not create the capture projection.')
        atomic_write_bytes(assets/f'{token}.png', png.tobytes())
        scene = {'schemaVersion': '0.1.0', 'id': project_id, 'name': project['name'], 'revision': 0,
                 'units': 'meters', 'upAxis': 'Y',
                 'source': {'imageUrl': f'{base_url}/{token}.png', 'imageWidth': 1024, 'imageHeight': 1024,
                            'mimeType': 'image/png', 'synthetic': bool(layout),
                            'capture': {'kind': project['source']['kind'], 'jobId': manifest['jobId'],
                                        'calibrationRevision': revision, 'representation': representation},
                            'calibration': {'pointA': [32, 32], 'pointB': [992, 32], 'distanceMeters': span,
                                            'metersPerPixel': meters_per_pixel,
                                            'notes': ['Generated top-down capture projection; not an uploaded blueprint.']}},
                 'reconstruction': {'parser': {'name': 'capture-editor', 'version': '1.0.0', 'checkpoint': None, 'license': None},
                                    'createdAt': datetime.now(timezone.utc).isoformat(),
                                    'defaults': {'wallHeight': 2.7, 'wallThickness': .12},
                                    'warnings': [*manifest.get('warnings', []),
                                        *(['Uncalibrated scan: 1 reconstruction unit is shown as 1 m. Set a known distance in Photos & video for real dimensions.'] if uncalibrated else []),
                                        *(['Floor not aligned: the scan may appear tilted. Align the floor in Photos & video.'] if unaligned else []),
                                        'Editor snapshot is independent of later capture reconstruction and calibration.',
                                        'Template walls and fixtures are editable.' if entities['walls'] else
                                        'The scan is one surface object. Add walls and furniture to build a semantic layout; scanned objects are not segmented.']},
                 **entities}
        problems = validate_scene(scene, contracts_dir/'scene.schema.json')
        if problems:
            raise ApiError(422, 'INVALID_EDITOR_SCENE', 'The capture cannot be opened as an editor scene.', problems)
        storage.save_reconstruction(project_id, scene)
        project['hasScene'] = True
        storage.save_project(project)
        return scene
