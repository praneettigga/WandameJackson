"""Multi-view depth consistency and TSDF fusion in one shared, arbitrary-scale frame."""
from __future__ import annotations
import json
import struct
from pathlib import Path
import numpy as np

# OpenCV's first camera (+Y down, +Z forward) to a right-handed viewer frame.
# This is a convention conversion, NOT a floor/gravity estimate.
WORLD_TO_VIEWER = np.diag([1., -1., -1., 1.])


def consistent_depths(depths, confidence, intrinsics, extrinsics, valid_pixels, progress,
                      relative_tolerance=.04, confidence_quantile=.25):
    n, h, w = depths.shape
    if n < 2 or intrinsics.shape != (n, 3, 3) or extrinsics.shape != (n, 4, 4):
        raise ValueError('Camera and depth dimensions do not agree.')
    if not np.isfinite(intrinsics).all() or not np.isfinite(extrinsics).all():
        raise ValueError('Non-finite camera predictions.')
    rotations = extrinsics[:, :3, :3]
    if not np.allclose(rotations @ rotations.transpose(0, 2, 1), np.eye(3), atol=1e-3) or not np.allclose(np.linalg.det(rotations), 1, atol=1e-3):
        raise ValueError('Camera rotations are not rigid.')
    if np.any(intrinsics[:, [0, 1], [0, 1]] <= 0):
        raise ValueError('Invalid focal length.')
    good = valid_pixels & np.isfinite(depths) & (depths > 0) & np.isfinite(confidence)
    if good.any():
        low, high = np.quantile(depths[good], [.005, .995])
        good &= (depths >= low) & (depths <= high)
    for i in range(n):
        if good[i].any():
            good[i] &= confidence[i] >= np.quantile(confidence[i][good[i]], confidence_quantile)
        # Never integrate silhouettes/depth discontinuities into bridging triangles.
        dx = np.abs(np.diff(depths[i], axis=1, prepend=depths[i, :, :1]))
        dy = np.abs(np.diff(depths[i], axis=0, prepend=depths[i, :1, :]))
        good[i] &= (dx < .05 * depths[i]) & (dy < .05 * depths[i])
    yy, xx = np.mgrid[:h, :w]
    pixels = np.stack([xx.ravel(), yy.ravel(), np.ones(h*w)])
    result = np.zeros_like(depths, dtype=np.float32)
    counts = []
    for i in range(n):
        progress(.40 + .20 * i/n, 'filtering_depth')
        ids = np.flatnonzero(good[i].ravel())
        points = (np.linalg.inv(intrinsics[i]) @ pixels[:, ids]) * depths[i].ravel()[ids]
        world = np.linalg.inv(extrinsics[i]) @ np.vstack([points, np.ones(len(ids))])
        supported = np.zeros(len(ids), dtype=bool)
        # All selected views share a VGGT world frame. Require another observation,
        # not just a high single-image model score, before a surface can be fused.
        for j in range(n):
            if i == j or not len(ids):
                continue
            camera = extrinsics[j] @ world
            z = camera[2]
            uv = intrinsics[j] @ camera[:3]
            u = np.rint(uv[0] / np.maximum(z, 1e-8)).astype(np.int64)
            v = np.rint(uv[1] / np.maximum(z, 1e-8)).astype(np.int64)
            inside = (z > 1e-6) & (u >= 0) & (v >= 0) & (u < w) & (v < h)
            k = np.flatnonzero(inside)
            sampled = depths[j, v[k], u[k]]
            agrees = good[j, v[k], u[k]] & (np.abs(sampled - z[k]) <= relative_tolerance * z[k])
            supported[k[agrees]] = True
        result[i].ravel()[ids[supported]] = depths[i].ravel()[ids[supported]]
        counts.append(int(supported.sum()))
    if sum(counts) < 1000:
        raise ValueError('Too little consistent surface evidence. Retake with more overlapping, textured views.')
    return result, {'supportedPixels': counts, 'supportedFraction': float(np.count_nonzero(result) / result.size),
                    'relativeDepthTolerance': relative_tolerance, 'confidenceQuantileRemoved': confidence_quantile,
                    'retainedDepthQuantiles': [.005, .995]}


def fuse_mesh(depths, rgb, intrinsics, extrinsics, progress):
    import open3d as o3d
    nonzero = depths[depths > 0]
    if not len(nonzero):
        raise ValueError('No supported depths to fuse.')
    median = float(np.median(nonzero))
    voxel = median / 256.0
    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=voxel, sdf_trunc=voxel * 4,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8)
    n, h, w = depths.shape
    for i in range(n):
        progress(.62 + .16 * i/n, 'fusing_surfaces')
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            o3d.geometry.Image(np.ascontiguousarray(rgb[i], dtype=np.uint8)),
            o3d.geometry.Image(np.ascontiguousarray(depths[i], dtype=np.float32)),
            depth_scale=1.0, depth_trunc=float(nonzero.max() * 1.01), convert_rgb_to_intensity=False)
        k = intrinsics[i]
        pinhole = o3d.camera.PinholeCameraIntrinsic(w, h, k[0, 0], k[1, 1], k[0, 2], k[1, 2])
        volume.integrate(rgbd, pinhole, extrinsics[i])  # world -> camera, no inversion
    progress(.80, 'extracting_mesh')
    mesh = volume.extract_triangle_mesh()
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_duplicated_vertices()
    if len(mesh.triangles):
        # Validate at the exported precision: distinct float64 TSDF vertices can
        # collapse to the same float32 position in GLB and create zero-area faces.
        corners = np.asarray(mesh.vertices, dtype=np.float32)[np.asarray(mesh.triangles)]
        areas = np.linalg.norm(np.cross(corners[:, 1]-corners[:, 0], corners[:, 2]-corners[:, 0]), axis=1)
        mesh.remove_triangles_by_mask(~np.isfinite(areas) | (areas <= 1e-12))
    if len(mesh.triangles):
        labels, counts, _ = mesh.cluster_connected_triangles()
        mesh.remove_triangles_by_mask(np.asarray(counts)[np.asarray(labels)] < 20)
    mesh.remove_unreferenced_vertices()
    if len(mesh.triangles) < 100 or not mesh.has_vertex_colors():
        raise ValueError('Not enough connected, colored triangles survived fusion. Try a slower, overlapping capture.')
    # No Poisson reconstruction, hole filling, convex hull, or unseen-region completion.
    mesh.transform(WORLD_TO_VIEWER)
    mesh.compute_vertex_normals()
    return mesh, {'voxelLength': voxel, 'sdfTruncation': voxel * 4,
                  'vertices': len(mesh.vertices), 'triangles': len(mesh.triangles)}


def write_glb(path: Path, vertices, triangles, colors, normals):
    """Small self-contained glTF 2.0 indexed triangle mesh with vertex colors."""
    vertices = np.asarray(vertices, dtype='<f4')
    normals = np.asarray(normals, dtype='<f4')
    triangles = np.asarray(triangles, dtype='<u4')
    colors = np.clip(np.asarray(colors, dtype='<f4'), 0, 1)
    # Open3D averages sRGB image colors; glTF vertex colors must be linear.
    colors = np.where(colors <= .04045, colors/12.92, ((colors+.055)/1.055)**2.4).astype('<f4')
    if vertices.shape != normals.shape or colors.shape != vertices.shape or vertices.shape[1:] != (3,):
        raise ValueError('Invalid vertex attributes.')
    if not all(np.isfinite(a).all() for a in (vertices, normals, colors)) or not len(triangles):
        raise ValueError('Empty or non-finite mesh.')
    if triangles.shape[1:] != (3,) or triangles.max() >= len(vertices):
        raise ValueError('Invalid triangle indices.')
    corners = vertices[triangles]
    areas = np.linalg.norm(np.cross(corners[:, 1]-corners[:, 0], corners[:, 2]-corners[:, 0]), axis=1)
    if not (areas > 1e-12).all():
        raise ValueError('Degenerate triangles at GLB float32 precision.')
    arrays = [vertices, normals, colors, triangles.ravel()]
    views, accessors, binary = [], [], bytearray()
    for i, arr in enumerate(arrays):
        raw = arr.tobytes()
        views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(raw), 'target': 34963 if i == 3 else 34962})
        accessor = {'bufferView': i, 'componentType': 5125 if i == 3 else 5126,
                    'count': len(arr), 'type': 'SCALAR' if i == 3 else 'VEC3'}
        if i == 0:
            accessor.update(min=arr.min(axis=0).tolist(), max=arr.max(axis=0).tolist())
        accessors.append(accessor)
        binary.extend(raw)
    doc = {'asset': {'version': '2.0', 'generator': 'ROOMSHIFT mesh-worker/1.0.0',
                     'extras': {'units': 'uncalibrated', 'provenance': 'inferred from imagery'}},
           'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
           'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 1, 'COLOR_0': 2}, 'indices': 3, 'material': 0, 'mode': 4}]}],
           'materials': [{'doubleSided': True, 'pbrMetallicRoughness': {'metallicFactor': 0, 'roughnessFactor': 1}}],
           'buffers': [{'byteLength': len(binary)}], 'bufferViews': views, 'accessors': accessors}
    payload = json.dumps(doc, separators=(',', ':'), allow_nan=False).encode()
    payload += b' ' * (-len(payload) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(payload) + 8 + len(binary)) +
                     struct.pack('<I4s', len(payload), b'JSON') + payload +
                     struct.pack('<I4s', len(binary), b'BIN\0') + binary)
