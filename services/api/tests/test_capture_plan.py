"""A capture mesh is drawn as a floor plan and parsed into the same Scene as a blueprint upload."""
import sys

import numpy as np
import pytest

from conftest import SCHEMA, wait_job
from eval.capture_generate import cameras, patch, polygon_room, tilted
from roomshift_api.capture_plan import PlanError, build_plan
from roomshift_api.config import REPO_ROOT
from roomshift_api.parser import parse_blueprint
from roomshift_api.storage import atomic_write_json
from roomshift_api.validation import validate_scene

sys.path.insert(0, str(REPO_ROOT / 'services/reconstruction'))
from geometry import write_glb  # noqa: E402

WIDTH, DEPTH, HEIGHT = 5.0, 4.0, 2.6


def room_mesh(doorway=False, walls=4):
    """A box room in meters, y up. `doorway` leaves a 0.9 m opening in the x=0 wall, seen through to a hall."""
    up = [0, HEIGHT, 0]
    parts = [patch(np.zeros(3), [WIDTH, 0, 0], [0, 0, DEPTH]), patch(np.array(up, float), [WIDTH, 0, 0], [0, 0, DEPTH]),
             patch(np.zeros(3), [WIDTH, 0, 0], up), patch(np.array([WIDTH, 0, 0.]), [0, 0, DEPTH], up),
             patch(np.array([0, 0, DEPTH]), [WIDTH, 0, 0], up)][:2 + walls]
    if walls == 4 and doorway:
        parts += [patch(np.zeros(3), [0, 0, 1.5], up), patch(np.array([0, 0, 2.4]), [0, 0, 1.6], up),
                  patch(np.array([-1.5, 0, 1.0]), [0, 0, 2.0], up)]  # far wall of the hall behind the doorway
    elif walls == 4:
        parts.append(patch(np.zeros(3), [0, 0, DEPTH], up))
    parts.append(patch(np.array([2.0, 0, 1.5]), [1.0, 0, 0], [0, 0.8, 0]))  # a table front: furniture, not a wall
    positions, triangles, offset = [], [], 0
    for grid, faces in parts:
        positions.append(grid)
        triangles.append(faces + offset)
        offset += len(grid)
    return np.vstack(positions), np.vstack(triangles)


def parsed(plan):
    return parse_blueprint(plan.image, plan.meters_per_pixel, plan.wall_height, None)


def test_tilted_unscaled_room_becomes_metric_rectangle():
    positions, triangles = room_mesh()
    plan = build_plan(*tilted(positions, cameras())[:1], triangles, tilted(positions, cameras())[1], metric=False)
    assert plan.scale_method == 'camera-height' and plan.ceiling_observed
    assert plan.scale == pytest.approx(1 / 0.37, rel=0.03)  # the camera was held at the assumed 1.5 m
    assert plan.wall_height == pytest.approx(HEIGHT, abs=0.1)
    result = parsed(plan)
    assert len(result['walls']) == 4 and len(result['rooms']) == 1 and not result['openings']
    xs, zs = zip(*result['rooms'][0]['polygon'])
    assert sorted([max(xs) - min(xs), max(zs) - min(zs)]) == pytest.approx([DEPTH, WIDTH], abs=0.15)
    # The mesh maps into the plan frame: the floor lands on y=0 inside the room polygon.
    floor = (np.c_[tilted(positions, cameras())[0][:50], np.ones(50)] @ plan.mesh_to_plan.T)[:, :3]
    assert np.abs(floor[:, 1]).max() < 0.02
    assert min(xs) - 0.1 <= floor[:, 0].min() and floor[:, 0].max() <= max(xs) + 0.1


def test_calibrated_mesh_keeps_its_scale():
    positions, triangles = room_mesh()
    plan = build_plan(positions, triangles, cameras(), metric=True)
    assert plan.scale == 1 and plan.scale_method == 'calibrated' and not plan.warnings


def test_open_doorway_becomes_a_door():
    positions, triangles = room_mesh(doorway=True)
    result = parsed(build_plan(positions, triangles, cameras(24), metric=True))
    assert len(result['rooms']) == 1
    assert [o['type'] for o in result['openings']] == ['door']
    assert result['openings'][0]['width'] == pytest.approx(0.9, abs=0.2)


def test_unseen_wall_is_closed_where_the_view_ends_with_a_warning():
    positions, triangles = room_mesh(walls=2)
    poses = cameras(24)
    facing = [p for p in poses if p[0, 2] > 0.3 or p[2, 2] < -0.3]  # only looking at the two captured walls
    plan = build_plan(positions, triangles, np.array(facing), metric=True)
    assert any('not seen clearly' in w for w in plan.warnings)
    assert len(parsed(plan)['rooms']) == 1


def test_unseen_wall_is_marked_low_confidence_in_the_scene():
    from eval.capture_generate import BOX, Room, capture
    from eval.capture_run import predict
    case = capture('room', Room(BOX), cameras(16, (1.5, 1.35, 2.0), start=60, sweep=240), 0.0)
    plan, scene = predict(case)
    assert sorted(round(s['observed'], 1) for s in plan.segments)[0] == 0
    scores = sorted(w['provenance']['confidence'] for w in scene['walls'])
    assert scores[0] <= 0.05 and scores[-1] >= 0.8
    unseen = min(scene['walls'], key=lambda w: w['provenance']['confidence'])
    assert unseen['provenance']['confidenceFactors'][0]['label'] == 'Seen in capture'
    assert any('not seen in the capture' in note for note in unseen['provenance']['notes'])
    assert not validate_scene(scene, SCHEMA)
    # The room is still closed at its real size: the two side walls were seen to their ends.
    xs, zs = zip(*scene['rooms'][0]['polygon'])
    assert sorted([max(xs) - min(xs), max(zs) - min(zs)]) == pytest.approx([4.0, 5.0], abs=0.2)


def test_room_seen_from_one_arm_keeps_its_l_shape():
    """The wall the L turns into faces away from the camera; the seen wall's end marks the corner."""
    from eval.capture_generate import Room, capture
    from eval.capture_run import predict
    room = Room([(0, 0), (6, 0), (6, 3), (3, 3), (3, 5), (0, 5)])
    _, scene = predict(capture('room', room, cameras(24, (4.5, 1.35, 1.5)), 0.0))
    # The camera was held lower than assumed, so the area carries the scale error; the shape is what matters.
    assert len(scene['walls']) == 6 and shoelace(scene['rooms'][0]['polygon']) == pytest.approx(24, rel=0.12)


def test_tall_furniture_is_not_a_wall():
    from eval.capture_generate import BOX, Room, capture
    from eval.capture_run import predict
    room = Room(BOX).box('cabinet', (4.55, 3.4), (0.9, 1.2), 2.3)
    _, scene = predict(capture('room', room, cameras(24, (2.0, 1.35, 1.6)), 0.0))
    assert len(scene['walls']) == 4 and [o['category'] for o in scene['objects']] == ['cabinet']


def test_door_height_sets_the_scale_when_doors_are_seen_whole():
    from roomshift_api.capture_plan import _door_top, _fuse_scale
    positions, triangles = room_mesh()
    moved, poses = tilted(positions, cameras())
    points, classes, views = labelled_room()  # its door is 2.05 m tall
    labels = (tilted(points, cameras())[0], classes, views)
    # The depth model is 12% out and the camera was held where assumed: the door pulls the scale back.
    plan = build_plan(moved, triangles, poses, metric=False, meters_per_unit=1.12 / 0.37, labels=labels)
    assert plan.scale_method == 'door-height' and plan.scale == pytest.approx(1 / 0.37, rel=0.03)
    assert 'the height of the doors' in plan.warnings[0]
    # A door whose lower part is hidden says nothing about its height.
    assert _door_top(np.linspace(1.2, 2.0, 500), np.arange(500) % 3) is None
    # An estimate far from the other two is left out.
    scale, method, _, used = _fuse_scale([('camera-height', 1.0, 0.15), ('depth-model', 1.05, 0.10), ('door-height', 1.6, 0.05)])
    assert used == ['depth-model', 'camera-height'] and method == 'depth-model' and 1.0 < scale < 1.05


def test_one_views_wrong_label_does_not_make_a_window():
    """A mirror called a window in two views of four is outvoted by the views that call it wall."""
    from roomshift_api.capture_plan import WALL, WINDOW
    positions, triangles = room_mesh()
    points, classes, views = labelled_room()
    mirror = (points[:, 2] == DEPTH) & (points[:, 0] > 1.5) & (points[:, 0] < 2.7) & (points[:, 1] > 0.8) & (points[:, 1] < 1.9)
    assert (classes[mirror] == WALL).all() and mirror.sum() > 200
    wrong = mirror & (views < 2)
    labels = (np.concatenate([points, points[wrong]]), np.concatenate([classes, np.full(wrong.sum(), WINDOW, np.uint8)]),
              np.concatenate([views, views[wrong]]))
    plan = build_plan(positions, triangles, cameras(), metric=True, labels=labels)
    assert sorted(o['type'] for o in plan.openings) == ['door', 'window']  # only the real ones


def test_window_on_an_angled_wall_reaches_the_scene():
    from eval.capture_generate import Room, capture
    from eval.capture_run import predict
    room = Room([(0, 0), (5, 0), (5, 2), (3, 4), (0, 4)]).window(2, 1.41, 1.2)
    plan, scene = predict(capture('room', room, cameras(24, (2.2, 1.35, 1.8)), 0.0))
    window, = scene['openings']
    wall = next(w for w in scene['walls'] if w['id'] == window['wallId'])
    assert min(abs(np.subtract(wall['end'], wall['start']))) > 0.5  # the angled wall
    assert window['type'] == 'window' and window['provenance']['source'] == 'capture-segmentation'
    assert window['bottom'] == pytest.approx(0.9 * plan.scale * 0.37, abs=0.15) and not validate_scene(scene, SCHEMA)


def test_door_standing_open_and_curtained_window_are_found():
    from eval.capture_generate import BOX, Room, capture
    from eval.capture_run import predict
    ajar = Room(BOX).door(3, 2.0, open=True, hall=False, leaf=True)
    _, scene = predict(capture('room', ajar, cameras(24, (2.8, 1.35, 2.0)), 0.0))
    assert [o['type'] for o in scene['openings']] == ['door']
    curtained = Room(BOX).window(1, 2.0, 1.6).curtain(1, 1.25, 0.5).curtain(1, 2.75, 0.5)
    plan, scene = predict(capture('room', curtained, cameras(24, (2.2, 1.35, 2.0)), 0.0))
    window, = scene['openings']
    # 1.0 m of glass shows; the curtains account for more of the 1.6 m window than the glass alone.
    assert 1.3 < window['width'] / (plan.scale * 0.37) < 2.1


def furnished(room, at, **options):
    from eval.capture_generate import capture
    from eval.capture_run import predict
    plan, scene = predict(capture('room', room, cameras(24, (at[0], 1.35, at[1])), 0.0, **options))
    return plan.scale * 0.37, scene


def test_touching_desks_stay_separate_when_the_worker_numbers_objects():
    from eval.capture_generate import BOX, Room
    room = Room(BOX).box('table', (1.6, 0.3), (1.2, 0.6), 0.75).box('table', (2.8, 0.3), (1.2, 0.6), 0.75)
    ratio, scene = furnished(room, (2.2, 2.4))
    assert [o['category'] for o in scene['objects']] == ['table', 'table']
    for desk in scene['objects']:
        assert sorted([desk['dimensions'][0], desk['dimensions'][2]]) == pytest.approx([0.6 * ratio, 1.2 * ratio], abs=0.15)
    # Labels written before objects were numbered still open: the 2.4 m run is cut by the length a table can be.
    _, older = furnished(room, (2.2, 2.4), numbered=False)
    assert [o['category'] for o in older['objects']] == ['table', 'table']


def test_long_bed_is_one_piece_and_angled_sofa_keeps_its_angle():
    from eval.capture_generate import BOX, Room
    room = Room(BOX).box('bed', (4.0, 3.25), (2.0, 1.5), 0.5).box('sofa', (1.6, 2.6), (2.0, 0.9), 0.8, np.deg2rad(30))
    ratio, scene = furnished(room, (2.8, 1.0), numbered=False)
    by_kind = {o['category']: o for o in scene['objects']}
    assert sorted(by_kind) == ['bed', 'sofa'] and len(scene['objects']) == 2
    assert by_kind['bed']['rotationY'] == 0
    sofa = by_kind['sofa']
    # The plan may put either room axis across the page, so the turn reads as 30 or 60 degrees.
    assert min(abs(abs(sofa['rotationY']) - np.deg2rad(t)) for t in (30, 60)) < np.deg2rad(6)
    assert sorted([sofa['dimensions'][0], sofa['dimensions'][2]]) == pytest.approx([0.9 * ratio, 2.0 * ratio], abs=0.2)
    assert not validate_scene(scene, SCHEMA)


def test_wardrobe_seen_from_the_front_gets_a_depth_against_the_wall():
    from eval.capture_generate import BOX, Room
    ratio, scene = furnished(Room(BOX).box('cabinet', (0.9, 3.7), (1.8, 0.6), 2.2), (1.0, 1.6))
    wardrobe, = scene['objects']
    wide, deep = sorted([wardrobe['dimensions'][0], wardrobe['dimensions'][2]], reverse=True)
    # Only its front was seen. It gets a cupboard's usual depth and is set back against the wall behind it.
    assert wide == pytest.approx(1.8 * ratio, abs=0.2) and deep == pytest.approx(0.6 * ratio, abs=0.12)
    assert wardrobe['provenance']['fieldOrigins'] == {'dimensions': 'inferred'}
    assert any('hidden from the camera' in note for note in wardrobe['provenance']['notes'])


def test_other_standing_things_are_boxed_and_named():
    from roomshift_api.capture_plan import OTHER
    positions, triangles = room_mesh()
    points, classes, views = labelled_room()
    plant = next(k for k, name in OTHER.items() if name == 'plant')
    grid = np.array([[x, h, z] for x in np.arange(4.0, 4.5, 0.05) for z in np.arange(3.2, 3.7, 0.05) for h in (0.4, 0.8, 1.1)])
    labels = (np.concatenate([points, grid]), np.concatenate([classes, np.full(len(grid), plant, np.uint8)]),
              np.concatenate([views, (np.arange(len(grid)) % 4).astype(np.uint16)]))
    plan = build_plan(positions, triangles, cameras(), metric=True, labels=labels)
    named = {o['label']: o['category'] for o in plan.objects}
    assert named == {'table': 'table', 'plant': 'other'}


def test_depth_model_weight_follows_how_much_its_views_agreed():
    positions, triangles = room_mesh()
    moved, poses = tilted(positions, cameras())
    hint = 1.12 / 0.37  # 12% out; the assumed camera height is right here
    agreed = build_plan(moved, triangles, poses, metric=False, meters_per_unit=hint, scale_spread=0.02)
    unsure = build_plan(moved, triangles, poses, metric=False, meters_per_unit=hint, scale_spread=0.4)
    # Views that agreed are trusted more (but never better than 5%); views that disagreed barely count.
    assert agreed.scale > unsure.scale and unsure.scale == pytest.approx(1 / 0.37, rel=0.03)
    assert agreed.scale_spread < unsure.scale_spread


def test_mesh_without_room_structure_is_rejected():
    grid, faces = patch(np.zeros(3), [2, 0, 0], [0, 2, 0])
    with pytest.raises(PlanError):
        build_plan(grid, faces, np.tile(np.eye(4), (12, 1, 1)), metric=False)


def seed_room(client, units='uncalibrated'):
    storage = client.app.state.storage
    root = storage.project_dir('p_room')
    positions, triangles = room_mesh()
    output = root / 'reconstructions/j_mesh'
    output.mkdir(parents=True)
    write_glb(output / 'mesh.glb', positions, triangles, np.full_like(positions, .5), np.tile([0, 1., 0], (len(positions), 1)))
    atomic_write_json(output / 'manifest.json', {
        'schemaVersion': '1.0.0', 'projectId': 'p_room', 'jobId': 'j_mesh', 'units': units, 'statistics': {},
        'warnings': [], 'cameras': [{'frameId': f'frame_{i}', 'cameraToWorld': pose.tolist()} for i, pose in enumerate(cameras())]})
    storage.save_project({'id': 'p_room', 'name': 'Bedroom', 'createdAt': '2026-11-15T00:00:00Z', 'hasScene': False,
                          'hasMesh': True, 'source': {'kind': 'video', 'originals': []},
                          'meshManifestPath': 'reconstructions/j_mesh/manifest.json',
                          'meshManifestUrl': '/api/projects/p_room/mesh'})
    return 'p_room'


def test_capture_opens_as_blueprint_style_scene_and_can_be_rescaled(client):
    pid = seed_room(client)
    response = client.post(f'/api/projects/{pid}/editor-scene')
    assert response.status_code == 200, response.text
    scene = response.json()
    assert not validate_scene(scene, SCHEMA)
    assert len(scene['walls']) == 4 and len(scene['rooms']) == 1 and not scene['objects']
    assert scene['source']['capture'] == {'kind': 'video', 'jobId': 'j_mesh', 'calibrationRevision': None, 'representation': 'layout'}
    assert scene['source']['calibration']['method'] == 'camera-height'
    assert scene['walls'][0]['height'] == pytest.approx(HEIGHT, abs=0.1)
    assert scene['walls'][0]['provenance']['fieldOrigins']['start'] == 'inferred'
    # The plan is the project's blueprint: the blueprint endpoints now serve this capture.
    envelope = client.get(f'/api/projects/{pid}').json()
    assert envelope['project']['hasScene'] and envelope['source']['kind'] == 'video'
    assert envelope['image']['url'] == scene['source']['imageUrl']
    assert client.get(envelope['image']['url']).headers['content-type'] == 'image/png'
    assert client.get(f'/api/projects/{pid}/scale').json()['calibration'] == scene['source']['calibration']
    assert client.post(f'/api/projects/{pid}/editor-scene').json() == scene  # reopening keeps the scene
    # A manual reference on the plan rescales the room through the ordinary blueprint job.
    reference = scene['source']['calibration']
    job = client.post(f'/api/projects/{pid}/reconstruct', json={'calibration': {
        'pointA': reference['pointA'], 'pointB': reference['pointB'], 'distanceMeters': reference['distanceMeters'] * 2}})
    assert job.status_code == 202, job.text
    assert wait_job(client, job.json()['job']['id'])['status'] == 'succeeded'
    rescaled = client.get(f'/api/projects/{pid}/scene').json()
    assert 'method' not in rescaled['source']['calibration']
    assert rescaled['source']['capture'] == scene['source']['capture']
    assert rescaled['walls'][0]['height'] == pytest.approx(2 * scene['walls'][0]['height'], abs=0.01)
    longest = max(np.hypot(*np.subtract(w['end'], w['start'])) for w in rescaled['walls'])
    assert longest == pytest.approx(2 * WIDTH, abs=0.3)


def test_depth_model_scale_is_used_when_plausible():
    positions, triangles = room_mesh()
    moved, poses = tilted(positions, cameras())
    plan = build_plan(moved, triangles, poses, metric=False, meters_per_unit=1 / 0.37 * 1.1)
    # The depth model outweighs the assumed camera height (which says 1 / 0.37 here) without replacing it.
    assert plan.scale_method == 'depth-model' and plan.scale == pytest.approx(1.068 / 0.37, rel=0.01)
    assert 0.05 < plan.scale_spread < 0.12 and 'the depth model and the height the camera' in plan.warnings[0]
    # An estimate that puts the camera at an impossible height is ignored.
    plan = build_plan(moved, triangles, poses, metric=False, meters_per_unit=30)
    assert plan.scale_method == 'camera-height'


def shoelace(polygon):
    x, z = np.asarray(polygon).T
    return abs(np.dot(x, np.roll(z, -1)) - np.dot(z, np.roll(x, -1))) / 2


def test_l_shaped_room_keeps_its_shape():
    positions, triangles = polygon_room([(0, 0), (6, 0), (6, 3), (3, 3), (3, 5), (0, 5)])
    poses = cameras(24)
    poses[:, :3, 3] = [2.0, 1.5, 2.0]
    result = parsed(build_plan(positions, triangles, poses, metric=True))
    assert len(result['rooms']) == 1 and len(result['walls']) == 6
    assert shoelace(result['rooms'][0]['polygon']) == pytest.approx(24, abs=1.0)


def test_angled_wall_is_kept_at_its_angle():
    positions, triangles = polygon_room([(0, 0), (5, 0), (5, 2), (3, 4), (0, 4)])
    plan = build_plan(positions, triangles, cameras(24), metric=True)
    slanted = [s for s in plan.segments if min(abs(np.subtract(s['end'], s['start']))) > 0.5]
    assert len(slanted) == 1 and np.hypot(*np.subtract(slanted[0]['end'], slanted[0]['start'])) == pytest.approx(2.83, abs=0.2)
    result = parsed(plan)
    assert len(result['rooms']) == 1 and len(result['walls']) == 5
    assert shoelace(result['rooms'][0]['polygon']) == pytest.approx(18, abs=1.0)


def labelled_room():
    """The box room with a closed door (z=0 wall), a window (x=WIDTH wall) and a table, as worker labels."""
    from roomshift_api.capture_plan import DOOR, TABLE, WALL, WINDOW
    points, classes = [], []
    step = np.arange(0.025, 10, 0.05)
    for x in step[step < WIDTH]:
        for h in step[step < HEIGHT]:
            points += [[x, h, 0], [x, h, DEPTH]]
            classes += [DOOR if 1.0 < x < 1.9 and h < 2.05 else WALL, WALL]
    for z in step[step < DEPTH]:
        for h in step[step < HEIGHT]:
            points += [[0, h, z], [WIDTH, h, z]]
            classes += [WALL, WINDOW if 1.2 < z < 2.6 and 0.9 < h < 2.0 else WALL]
    for x in step[(step > 2.0) & (step < 3.2)]:
        for z in step[(step > 1.5) & (step < 2.3)]:
            points.append([x, 0.75, z])
            classes.append(TABLE)
    points, classes = np.array(points), np.array(classes, np.uint8)
    return points, classes, (np.arange(len(points)) % 4).astype(np.uint16)


def test_labelled_door_window_and_table_reach_the_plan():
    positions, triangles = room_mesh()
    plan = build_plan(positions, triangles, cameras(), metric=True, labels=labelled_room())
    kinds = {o['type']: o for o in plan.openings}
    assert set(kinds) == {'door', 'window'} and len(plan.openings) == 2
    assert kinds['door']['width'] == pytest.approx(0.9, abs=0.15) and kinds['door']['bottom'] == 0
    assert kinds['door']['height'] == pytest.approx(2.05, abs=0.15)
    assert kinds['window']['width'] == pytest.approx(1.4, abs=0.15)
    assert kinds['window']['bottom'] == pytest.approx(0.9, abs=0.1) and kinds['window']['height'] == pytest.approx(1.1, abs=0.15)
    assert [o['category'] for o in plan.objects] == ['table']
    wide, tall, deep = plan.objects[0]['dimensions']  # the plan's x axis may be either of the room's
    assert tall == pytest.approx(0.75, abs=0.1) and sorted([wide, deep]) == pytest.approx([0.8, 1.2], abs=0.15)
    # The drawing says the same thing to the blueprint parser.
    result = parsed(plan)
    assert sorted((o['type'], round(o['width'], 1)) for o in result['openings']) == [('door', pytest.approx(0.9, abs=0.15)), ('window', pytest.approx(1.4, abs=0.15))]
    assert len(result['rooms']) == 1


def test_labelled_capture_scene_has_openings_and_furniture_that_survive_rescaling(client):
    pid = seed_room(client)
    points, classes, views = labelled_room()
    np.savez_compressed(client.app.state.storage.project_dir(pid) / 'reconstructions/j_mesh/labels.npz',
                        points=points.astype(np.float32), classes=classes, views=views)
    scene = client.post(f'/api/projects/{pid}/editor-scene').json()
    assert not validate_scene(scene, SCHEMA)
    by_type = {o['type']: o for o in scene['openings']}
    assert set(by_type) == {'door', 'window'}
    assert by_type['window']['bottom'] == pytest.approx(0.9, abs=0.1) and by_type['window']['height'] == pytest.approx(1.1, abs=0.15)
    assert by_type['door']['bottom'] == 0 and by_type['door']['height'] == pytest.approx(2.05, abs=0.15)
    assert by_type['window']['provenance']['source'] == 'capture-segmentation'
    table, = scene['objects']
    assert table['category'] == 'table' and table['componentId'] == 'table.basic' and table['position'][1] == 0
    assert table['dimensions'][1] == pytest.approx(0.75, abs=0.1)
    # Furniture stands inside the room it was seen in.
    xs, zs = zip(*scene['rooms'][0]['polygon'])
    assert min(xs) < table['position'][0] < max(xs) and min(zs) < table['position'][2] < max(zs)
    reference = scene['source']['calibration']
    job = client.post(f'/api/projects/{pid}/reconstruct', json={'calibration': {
        'pointA': reference['pointA'], 'pointB': reference['pointB'], 'distanceMeters': reference['distanceMeters'] * 2}})
    assert wait_job(client, job.json()['job']['id'])['status'] == 'succeeded'
    doubled = client.get(f'/api/projects/{pid}/scene').json()
    twice = {o['type']: o for o in doubled['openings']}
    assert twice['window']['bottom'] == pytest.approx(2 * by_type['window']['bottom'], abs=0.02)
    assert twice['window']['width'] == pytest.approx(2 * by_type['window']['width'], abs=0.1)
    bigger, = doubled['objects']
    assert bigger['position'] == pytest.approx([2 * v for v in table['position']], abs=0.01)
    assert bigger['dimensions'] == pytest.approx([2 * v for v in table['dimensions']], abs=0.01)
