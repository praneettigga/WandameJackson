"""Bake a demo video offline with high-quality Meshroom settings.

    python bake_demo.py VIDEO NAME [--frames 80] [--reference ax,ay,az,bx,by,bz,meters]
                                   [--floor x1,y1,z1,x2,y2,z2,x3,y3,z3] [--rotation rx,ry,rz]

Writes demo/NAME/{mesh.glb,diagnostic.ply,manifest.json,calibration.json} and registers the
video's SHA-256 in demo/presets.json. With ROOMSHIFT_DEMO_PRESETS=1, uploading that exact
video in the app serves this mesh instead of running Meshroom live.

Points are in the uncalibrated mesh.glb coordinates (pick them in the app's viewer with
"Measure", or in Blender/MeshLab), so the mesh opens already scaled and floor-aligned.
Re-run with only the calibration flags and --skip-meshroom to change calibration.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meshroom_worker import (DEMO_DIR, HIGH_QUALITY, export_mesh, manifest_for, read_cameras,  # noqa: E402
                             run_meshroom, save_json)


def floats(text, count):
    values = [float(v) for v in text.split(',')]
    if len(values) != count:
        raise argparse.ArgumentTypeError(f'expected {count} comma-separated numbers')
    return values


def extract_frames(video, images, count):
    duration = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(video)],
                                    capture_output=True, text=True, check=True).stdout)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(video), '-vf', f'fps={count / duration}',
                    '-frames:v', str(count), str(images / 'frame_%04d.png')], check=True)
    return sorted(images.glob('*.png'))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('video', type=Path)
    parser.add_argument('name')
    parser.add_argument('--frames', type=int, default=80)
    parser.add_argument('--reference', type=lambda t: floats(t, 7))
    parser.add_argument('--floor', type=lambda t: floats(t, 9))
    parser.add_argument('--rotation', type=lambda t: floats(t, 3), default=[0, 0, 0])
    parser.add_argument('--skip-meshroom', action='store_true', help='only update calibration.json')
    args = parser.parse_args()
    target = DEMO_DIR / args.name
    target.mkdir(parents=True, exist_ok=True)
    sha256 = hashlib.sha256(args.video.read_bytes()).hexdigest()

    if not args.skip_meshroom:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='roomshift-bake-') as tmp:
            work = Path(tmp)
            images = work / 'images'
            images.mkdir()
            paths = extract_frames(args.video, images, args.frames)
            frames = [{'id': p.stem, 'path': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
            print(f'{len(frames)} frames → Meshroom (high quality); this can take a long time…', flush=True)
            def progress(value, stage):
                print(f'  {value:4.0%} {stage}', flush=True)
            mesh_dir, cache = run_meshroom(images, work, progress, HIGH_QUALITY)
            stats = export_mesh(mesh_dir, target)
            cameras = read_cameras(cache, frames)
        stats['workerSeconds'] = time.monotonic() - started
        request = {'jobId': 'bake', 'input': {'projectId': 'demo', 'jobId': 'bake', 'frames': frames}}
        manifest = manifest_for(request, cameras, stats, {'settings': HIGH_QUALITY, 'bakedFrames': len(frames)})
        save_json(target / 'manifest.json', manifest)
        registered = sum(c['registered'] for c in cameras)
        print(f'Mesh: {stats["triangles"]} triangles, {registered}/{len(cameras)} views registered.')
        if registered < 3:
            raise SystemExit('Too few registered views to serve this preset.')

    calibration = {'rotationDegrees': args.rotation,
                   'reference': {'pointA': args.reference[:3], 'pointB': args.reference[3:6],
                                 'distanceMeters': args.reference[6]} if args.reference else None,
                   'floor': {'points': [args.floor[0:3], args.floor[3:6], args.floor[6:9]]} if args.floor else None}
    save_json(target / 'calibration.json', calibration)

    presets_path = DEMO_DIR / 'presets.json'
    presets = json.loads(presets_path.read_text()) if presets_path.is_file() else []
    presets = [p for p in presets if p['name'] != args.name and p['sha256'] != sha256]
    presets.append({'name': args.name, 'sha256': sha256, 'video': args.video.name})
    save_json(presets_path, presets)
    print(f'Registered preset "{args.name}" ({sha256[:12]}…) in {presets_path}')


if __name__ == '__main__':
    if not shutil.which('ffmpeg'):
        raise SystemExit('Install FFmpeg first.')
    main()
