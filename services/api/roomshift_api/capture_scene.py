"""Persistent editor scenes of a capture.

A capture opens as an editable layout: its mesh is drawn as a floor plan (`capture_plan`) and that plan
goes through the blueprint parser. When no room structure can be found, the mesh opens as one surface object.
"""
import uuid

import cv2
import numpy as np

from .calibration import compute_calibration
from .capture_plan import PLAN_VERSION, PlanError, build_plan, read_mesh
from .errors import ApiError
from .mesh_calibration import current_mesh
from .parser import ParseError
from .scene_builder import blueprint_scene
from .storage import atomic_write_bytes, now_iso
from .validation import validate_scene


def provenance():
    return {'origin': 'inferred', 'confidence': None, 'source': 'capture-mesh', 'userEdited': False, 'fieldOrigins': {},
            'notes': ['Unsegmented reconstructed surface. Individual walls and furniture are not identified.']}


def plan_project(project, manifest, plan, labelled):
    """The project fields and scale calibration that present `plan` as the project's blueprint."""
    tall, width = plan.image.shape
    # The reference line is the longest drawn wall, so the user can check it against the room.
    a, b = max(((np.asarray(s['start']), np.asarray(s['end'])) for s in plan.segments),
               key=lambda s: np.linalg.norm(s[1]-s[0]))
    calibration = compute_calibration((a/plan.meters_per_pixel).tolist(), (b/plan.meters_per_pixel).tolist(),
                                      float(np.linalg.norm(b-a)), width, tall)
    if plan.scale_method != 'calibrated':
        calibration.update(method=plan.scale_method, notes=[plan.warnings[0]])
    drawn = {**project,
             'image': {'url': f"/api/projects/{project['id']}/blueprint", 'width': width, 'height': tall, 'mimeType': 'image/png'},
             'automaticCalibration': calibration,
             'capturePlan': {'version': PLAN_VERSION, 'jobId': manifest['jobId'],
                             'calibrationRevision': manifest.get('calibrationRevision'),
                             'metersPerPixel': plan.meters_per_pixel, 'wallHeight': plan.wall_height,
                             'ceilingObserved': plan.ceiling_observed, 'scale': plan.scale, 'scaleSpread': round(plan.scale_spread, 3),
                             'meshToPlan': plan.mesh_to_plan.tolist(),
                             'openings': plan.openings, 'objects': plan.objects, 'segments': plan.segments,
                             'warnings': ['Floor plan drawn from the captured room: only surfaces the camera saw are known. ' +
                                          ('Doors, windows and furniture are placed where the views show them; check them against the room.'
                                           if labelled else
                                           'Doors, windows and furniture were not labelled; add them with the editor tools.'),
                                          *plan.warnings[1 if plan.scale_method != 'calibrated' else 0:],
                                          *manifest.get('warnings', [])]}}
    return drawn, calibration


def plan_scene(storage, project, manifest, mesh_path, contracts_dir):
    """Draw the mesh as a floor plan, store it as the project's blueprint and parse it into a Scene.

    Returns (scene, None), or (None, reason) when the capture has no usable room structure.
    """
    cameras = [c['cameraToWorld'] for c in manifest['cameras'] if c.get('registered', True)]
    labels = None
    labels_path = (storage.project_dir(project['id'])/project['meshManifestPath']).with_name('labels.npz')
    if labels_path.is_file():
        # Labelled points live in the raw reconstruction frame; a mesh calibration moves them with the mesh.
        with np.load(labels_path) as stored:
            matrix = np.asarray(manifest.get('reconstructionToWorld', np.eye(4)), dtype=float)
            labels = (stored['points'].astype(float) @ matrix[:3, :3].T + matrix[:3, 3], stored['classes'], stored['views'],
                      *([stored['instances']] if 'instances' in stored.files else []))
    try:
        plan = build_plan(*read_mesh(mesh_path), cameras, manifest['units'] == 'meters',
                          manifest.get('estimatedMetersPerUnit'), labels, scale_spread=manifest.get('estimatedScaleSpread'))
    except PlanError as error:
        return None, str(error)
    ok, png = cv2.imencode('.png', plan.image)
    if not ok:
        return None, 'The floor plan image could not be written.'
    drawn, calibration = plan_project(project, manifest, plan, labels is not None)
    try:
        scene = blueprint_scene(drawn, plan.image, calibration, plan.wall_height, None)
    except ParseError as error:
        return None, str(error)
    if not scene['rooms'] or validate_scene(scene, contracts_dir/'scene.schema.json'):
        return None, 'The captured walls do not enclose a room.'
    storage.save_blueprint(project['id'], png.tobytes(), 'image/png')
    project.update(drawn)
    return scene, None


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
        scene, plan_failure = plan_scene(storage, project, manifest, mesh_path, contracts_dir)
        if scene:
            storage.save_reconstruction(project_id, scene)
            project['hasScene'] = True
            storage.save_project(project)
            return scene
        positions = read_mesh(mesh_path)[0]
        low, high = positions.min(axis=0), positions.max(axis=0)
        # No room structure was found: open the scan as one surface object instead.
        unaligned = not (manifest.get('calibration') or {}).get('floor')
        token = uuid.uuid4().hex
        assets = root/'editor-assets'
        base_url = f'/api/projects/{project_id}/editor-assets'
        filename = f'{token}.glb'
        atomic_write_bytes(assets/filename, mesh_path.read_bytes())
        entities = {'rooms': [], 'walls': [], 'openings': [], 'objects': [{
            'id': 'capture-mesh', 'name': 'Captured room mesh', 'category': 'scan',
            'componentId': None, 'assetUrl': f'{base_url}/{filename}',
            'position': [float((low[0]+high[0])/2), float(low[1]), float((low[2]+high[2])/2)],
            'rotationY': 0, 'dimensions': np.maximum(high-low, 1e-6).tolist(),
            'provenance': provenance(),
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
                            'mimeType': 'image/png', 'synthetic': False,
                            'capture': {'kind': project['source']['kind'], 'jobId': manifest['jobId'],
                                        'calibrationRevision': revision, 'representation': 'mesh'},
                            'calibration': {'pointA': [32, 32], 'pointB': [992, 32], 'distanceMeters': span,
                                            'metersPerPixel': meters_per_pixel,
                                            'notes': ['Generated top-down capture projection; not an uploaded blueprint.']}},
                 'reconstruction': {'parser': {'name': 'capture-editor', 'version': '1.0.0', 'checkpoint': None, 'license': None},
                                    'createdAt': now_iso(),
                                    'defaults': {'wallHeight': 2.7, 'wallThickness': .12},
                                    'warnings': [*manifest.get('warnings', []),
                                        *(['Uncalibrated scan: 1 reconstruction unit is shown as 1 m. Set a known distance in Photos & video for real dimensions.'] if uncalibrated else []),
                                        *(['Floor not aligned: the scan may appear tilted. Align the floor in Photos & video.'] if unaligned else []),
                                        *([f'No editable walls were created: {plan_failure}'] if plan_failure else []),
                                        'Editor snapshot is independent of later capture reconstruction and calibration.',
                                        'The scan is one surface object. Add walls and furniture to build a semantic layout; scanned objects are not segmented.']},
                 **entities}
        problems = validate_scene(scene, contracts_dir/'scene.schema.json')
        if problems:
            for orphan in assets.glob(f'{token}.*'):
                orphan.unlink(missing_ok=True)
            raise ApiError(422, 'INVALID_EDITOR_SCENE', 'The capture cannot be opened as an editor scene.', problems)
        storage.save_reconstruction(project_id, scene)
        project['hasScene'] = True
        storage.save_project(project)
        return scene
