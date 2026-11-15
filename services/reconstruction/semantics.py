"""Per-pixel room semantics for a capture: which surfaces are wall, floor, door, window or furniture.

A small ADE20K segmentation model (OneFormer Swin-Tiny, MIT) labels every selected view on CPU. The
labels are attached to the depth points and saved beside the mesh, so the API can place doors,
windows and furniture on the floor plan without loading a model itself.
"""
from __future__ import annotations

import numpy as np

MODEL = 'shi-labs/oneformer_ade20k_swin_tiny'
MODEL_REVISION = '05f2812b1eccf9909b3897777450f8d68148cafc'
MODEL_LICENSE = 'MIT'
SEGMENT_SIDE = 512

# Class ids stored in labels.npz. Furniture names match the Scene object categories.
CLASSES = ['unknown', 'wall', 'floor', 'ceiling', 'door', 'window', 'chair', 'table', 'sofa', 'bed', 'cabinet', 'wall-fixture',
           'curtain', 'lamp', 'sink', 'toilet', 'bathtub', 'stove', 'plant', 'washer']
# ADE20K names (any comma-separated synonym) -> class. Things fixed flat to a wall count as wall evidence.
ADE_NAMES = {
    'wall': {'wall'},
    'floor': {'floor', 'rug'},
    'ceiling': {'ceiling'},
    'door': {'door', 'screen door'},
    'window': {'window', 'windowpane'},
    'chair': {'chair', 'armchair', 'swivel chair', 'stool', 'seat', 'bench', 'ottoman'},
    'table': {'table', 'desk', 'coffee table', 'counter', 'kitchen island', 'pool table', 'countertop'},
    'sofa': {'sofa'},
    'bed': {'bed', 'cradle'},
    'cabinet': {'cabinet', 'wardrobe', 'chest of drawers', 'bookcase', 'shelf', 'refrigerator', 'buffet', 'case'},
    'wall-fixture': {'painting', 'poster', 'mirror', 'blackboard', 'bulletin board',
                     'screen', 'crt screen', 'monitor', 'television receiver', 'radiator'},
    # May hang in front of a window: wall evidence, but never evidence against glass behind it.
    'curtain': {'curtain', 'blind', 'shower curtain'},
    # Standing pieces without a furniture category of their own; the editor shows them as named boxes.
    'lamp': {'lamp'},
    'sink': {'sink'},
    'toilet': {'toilet'},
    'bathtub': {'bathtub', 'shower'},
    'stove': {'stove', 'oven', 'dishwasher', 'microwave'},
    'plant': {'plant', 'flower', 'pot'},
    'washer': {'washer'},
}


def class_table(id2label: dict) -> np.ndarray:
    """Lookup from the model's label ids to CLASSES ids."""
    table = np.zeros(max(int(i) for i in id2label) + 1, np.uint8)
    for index, name in id2label.items():
        synonyms = {part.strip().lower() for part in str(name).split(',')}
        for target, names in ADE_NAMES.items():
            if synonyms & names:
                table[int(index)] = CLASSES.index(target)
                break
    return table


def segment(paths, shape, progress, low=.50, high=.62, instances=True):
    """(class map, instance map) for every image, resized to the depth maps' (height, width).

    Classes are uint8 CLASSES ids. Instances (uint8, 0 = none) number the separate objects within one
    image, from a second, panoptic pass of the same model: two chairs side by side get two numbers.
    A pixel keeps its instance only where that pass agrees with the class map.
    """
    import torch
    from PIL import Image
    from transformers import OneFormerForUniversalSegmentation, OneFormerProcessor

    processor = OneFormerProcessor.from_pretrained(MODEL, revision=MODEL_REVISION)
    model = OneFormerForUniversalSegmentation.from_pretrained(MODEL, revision=MODEL_REVISION).eval()
    table = class_table(model.config.id2label)
    height, width = shape
    result = np.zeros((len(paths), height, width), np.uint8)
    objects = np.zeros((len(paths), height, width), np.uint8)
    for i, path in enumerate(paths):
        progress(low + (high - low) * i / len(paths), 'labelling_surfaces')
        image = Image.open(path).convert('RGB')
        factor = SEGMENT_SIDE / max(image.size)
        image = image.resize((round(image.width * factor), round(image.height * factor)))
        with torch.inference_mode():
            output = model(**processor(images=image, task_inputs=['semantic'], return_tensors='pt'))
        labels = processor.post_process_semantic_segmentation(output, target_sizes=[(height, width)])[0]
        result[i] = table[labels.numpy()]
        if instances:
            with torch.inference_mode():
                output = model(**processor(images=image, task_inputs=['panoptic'], return_tensors='pt'))
            found = processor.post_process_panoptic_segmentation(output, target_sizes=[(height, width)], label_ids_to_fuse=set())[0]
            objects[i] = instance_map(found['segmentation'].numpy(), found['segments_info'], table, result[i])
    return result, objects


def instance_map(segmentation, segments, table, classes):
    """Number the panoptic segments of one image (1..255), keeping only pixels whose class map agrees."""
    numbered = np.zeros(classes.shape, np.uint8)
    for number, info in enumerate(segments[:255], start=1):
        numbered[(segmentation == info['id']) & (classes == table[info['label_id']]) & (classes > 0)] = number
    return numbered


def labelled_points(depths, confidence, classes, intrinsics, extrinsics, to_viewer, stride=3, discontinuity=.08, instances=None):
    """Back-project labelled pixels into the mesh frame: (points float32 (n, 3), class uint8, view uint16,
    instance uint8). An instance number is only unique within its view; 0 means none.

    Uses every confident pixel off a depth edge, not only multi-view-consistent ones: a wall seen once
    still belongs on the plan. `extrinsics` are OpenCV world-to-camera; `to_viewer` maps world to mesh.
    """
    points, labels, views, objects = [], [], [], []
    count, height, width = depths.shape
    yy, xx = np.mgrid[:height:stride, :width:stride]
    for i in range(count):
        depth = depths[i]
        good = np.isfinite(depth) & (depth > 0)
        if good.any():
            good &= confidence[i] >= np.quantile(confidence[i][good], .10)
        dx = np.abs(np.diff(depth, axis=1, prepend=depth[:, :1]))
        dy = np.abs(np.diff(depth, axis=0, prepend=depth[:1, :]))
        good &= (dx < discontinuity * depth) & (dy < discontinuity * depth)
        keep = good[yy, xx] & (classes[i][yy, xx] > 0)
        u, v, z = xx[keep], yy[keep], depth[yy, xx][keep]
        camera = np.linalg.inv(intrinsics[i]) @ np.stack([u * z, v * z, z])
        world = np.linalg.inv(extrinsics[i]) @ np.vstack([camera, np.ones(len(z))])
        points.append((to_viewer @ world)[:3].T.astype(np.float32))
        labels.append(classes[i][yy, xx][keep])
        views.append(np.full(len(z), i, np.uint16))
        objects.append(instances[i][yy, xx][keep] if instances is not None else np.zeros(len(z), np.uint8))
    return np.concatenate(points), np.concatenate(labels), np.concatenate(views), np.concatenate(objects)
