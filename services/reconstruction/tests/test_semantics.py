"""Label mapping and back-projection; no model is loaded here."""
import numpy as np

import semantics


def test_ade_names_map_to_room_classes():
    names = {0: 'wall', 3: 'floor', 5: 'ceiling', 8: 'window ', 14: 'door', 15: 'table', 19: 'chair',
             22: 'painting, picture', 33: 'desk', 10: 'cabinet', 7: 'bed', 23: 'sofa', 4: 'tree', 12: 'person',
             18: 'curtain, drape', 65: 'toilet, can', 71: 'stove, kitchen stove', 17: 'plant, flora'}
    table = semantics.class_table(names)
    mapped = {name: semantics.CLASSES[table[index]] for index, name in names.items()}
    assert mapped == {'wall': 'wall', 'floor': 'floor', 'ceiling': 'ceiling', 'window ': 'window', 'door': 'door',
                      'table': 'table', 'chair': 'chair', 'painting, picture': 'wall-fixture', 'desk': 'table',
                      'cabinet': 'cabinet', 'bed': 'bed', 'sofa': 'sofa', 'tree': 'unknown', 'person': 'unknown',
                      'curtain, drape': 'curtain', 'toilet, can': 'toilet', 'stove, kitchen stove': 'stove',
                      'plant, flora': 'plant'}
    # Ids already written to labels.npz files keep their meaning.
    assert semantics.CLASSES[:12] == ['unknown', 'wall', 'floor', 'ceiling', 'door', 'window', 'chair', 'table', 'sofa',
                                      'bed', 'cabinet', 'wall-fixture']


def test_instances_number_objects_only_where_the_class_map_agrees():
    chair = semantics.CLASSES.index('chair')
    table = np.zeros(30, np.uint8)
    table[19] = chair
    classes = np.zeros((4, 6), np.uint8)
    classes[:, :5] = chair
    segmentation = np.array([[7, 7, 9, 9, 3, 3]] * 4)
    segments = [{'id': 7, 'label_id': 19}, {'id': 9, 'label_id': 19}, {'id': 3, 'label_id': 0}]
    numbered = semantics.instance_map(segmentation, segments, table, classes)
    # Two chairs get two numbers; the third segment is not a chair, so the chair pixel under it has none.
    assert numbered[0].tolist() == [1, 1, 2, 2, 0, 0]


def test_labelled_points_land_where_the_depth_says():
    # One camera at the origin looking down +z at a flat wall 2 units away; the right half is a door.
    height, width = 30, 40
    depth = np.full((1, height, width), 2.0, np.float32)
    depth[0, :, :3] = 0  # no depth: dropped
    classes = np.ones((1, height, width), np.uint8)
    classes[0, :, width // 2:] = semantics.CLASSES.index('door')
    classes[0, :3] = 0  # unlabelled: dropped
    k = np.array([[[20., 0, width / 2], [0, 20., height / 2], [0, 0, 1]]])
    points, labels, views, numbers = semantics.labelled_points(depth, np.ones_like(depth), classes, k, np.eye(4)[None],
                                                      np.diag([1., -1., -1., 1.]), stride=1)
    assert len(points) == len(labels) == len(views) == len(numbers) and set(views) == {0} and set(numbers) == {0}
    assert np.allclose(points[:, 2], -2)  # viewer frame: z points back
    assert set(labels) == {1, semantics.CLASSES.index('door')}
    assert (points[labels == 1][:, 0] < 0).all() and (points[labels != 1][:, 0] >= 0).all()
    assert points[:, 1].max() < 1.5 - 3 * 0.1 + 1e-6  # the three unlabelled top rows are gone (y is up)
