"""Offline geometry tuning on cached VGGT outputs (CPU only, no model reload).

Dump outputs once with ROOMSHIFT_DUMP_NPZ=/path/capture.npz set for the worker, then:

    python tune.py capture.npz --out tuned.glb --tolerance .08 --voxel-divisor 192
"""
import argparse
import json
from pathlib import Path
import numpy as np
from geometry import consistent_depths, fuse_mesh, write_glb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('npz', type=Path)
    parser.add_argument('--out', type=Path, default=Path('tuned.glb'))
    parser.add_argument('--tolerance', type=float, default=.08)
    parser.add_argument('--confidence-quantile', type=float, default=.10)
    parser.add_argument('--discontinuity', type=float, default=.12)
    parser.add_argument('--single-view-quantile', type=float, default=.70, help='1 disables single-view fallback')
    parser.add_argument('--voxel-divisor', type=float, default=192.)
    parser.add_argument('--truncation-voxels', type=float, default=3.)
    parser.add_argument('--smoothing', type=int, default=10)
    parser.add_argument('--max-triangles', type=int, default=250_000)
    parser.add_argument('--min-component-fraction', type=float, default=.02)
    args = parser.parse_args()
    data = np.load(args.npz)
    quiet = lambda *_: None
    depth, evidence = consistent_depths(
        data['depth'], data['conf'], data['k'], data['e'], data['valid'], quiet,
        relative_tolerance=args.tolerance, confidence_quantile=args.confidence_quantile,
        discontinuity=args.discontinuity, single_view_quantile=args.single_view_quantile)
    mesh, stats = fuse_mesh(
        depth, data['rgb'], data['k'], data['e'], quiet, voxel_divisor=args.voxel_divisor,
        truncation_voxels=args.truncation_voxels, smoothing_iterations=args.smoothing,
        max_triangles=args.max_triangles, min_component_fraction=args.min_component_fraction)
    write_glb(args.out, mesh.vertices, mesh.triangles, mesh.vertex_colors, mesh.vertex_normals)
    print(json.dumps({'supportedFraction': evidence['supportedFraction'],
                      'supportedPixels': sum(evidence['supportedPixels']),
                      'singleViewPixels': sum(evidence['singleViewPixels']), **stats, 'out': str(args.out)}, indent=2))


if __name__ == '__main__':
    main()
