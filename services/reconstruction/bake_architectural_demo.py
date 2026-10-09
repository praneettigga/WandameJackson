"""Create a transparent, precomputed architectural demo preset for one exact video.

This is intentionally a modeled demo artifact, not a photogrammetry result.  It uses
a scene JSON layout to produce an open-top walls/floors/doors/windows GLB and registers
the SHA-256 of the supplied video in demo/presets.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from geometry import write_glb
from meshroom_worker import DEMO_DIR, save_json, vertex_normals


def add_box(vertices, faces, colors, low, high, color):
    base = len(vertices)
    x0, y0, z0 = low
    x1, y1, z1 = high
    vertices.extend([(x0,y0,z0), (x1,y0,z0), (x1,y1,z0), (x0,y1,z0),
                     (x0,y0,z1), (x1,y0,z1), (x1,y1,z1), (x0,y1,z1)])
    faces.extend([(0,2,1), (0,3,2), (4,5,6), (4,6,7), (0,1,5), (0,5,4),
                  (1,2,6), (1,6,5), (2,3,7), (2,7,6), (3,0,4), (3,4,7)])
    faces[-12:] = [tuple(base + index for index in face) for face in faces[-12:]]
    colors.extend([color] * 8)


def write_ply(path, vertices, faces, colors):
    header = ('ply\nformat ascii 1.0\n'
              f'element vertex {len(vertices)}\nproperty float x\nproperty float y\nproperty float z\n'
              'property uchar red\nproperty uchar green\nproperty uchar blue\n'
              f'element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n')
    rows = [header]
    rows += [f'{x:.6f} {y:.6f} {z:.6f} {round(r*255)} {round(g*255)} {round(b*255)}\n'
             for (x,y,z), (r,g,b) in zip(vertices, colors)]
    rows += [f'3 {a} {b} {c}\n' for a,b,c in faces]
    path.write_text(''.join(rows))


def wall_box(vertices, faces, colors, wall, start, end, bottom, height, color, extra=0):
    """Add one axis-aligned wall section, measured along the wall centreline."""
    (x0, z0), (x1, z1) = wall['start'], wall['end']
    length = max(((x1-x0)**2 + (z1-z0)**2) ** .5, 1e-6)
    ax, az = x0 + (x1-x0) * start/length, z0 + (z1-z0) * start/length
    bx, bz = x0 + (x1-x0) * end/length, z0 + (z1-z0) * end/length
    thickness = wall['thickness'] + extra
    if abs(x1-x0) >= abs(z1-z0):
        low, high = (min(ax,bx), bottom, z0-thickness/2), (max(ax,bx), bottom+height, z0+thickness/2)
    else:
        low, high = (x0-thickness/2, bottom, min(az,bz)), (x0+thickness/2, bottom+height, max(az,bz))
    add_box(vertices, faces, colors, low, high, color)


def build(scene):
    vertices, faces, colors = [], [], []
    for room in scene['rooms']:
        xs, zs = zip(*room['polygon'])
        # A modest tiled floor keeps the demo visually architectural and provides
        # sufficient non-degenerate geometry for the normal mesh publication gate.
        x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
        for ix in range(6):
            for iz in range(5):
                xa, xb = x0 + (x1-x0) * ix/6, x0 + (x1-x0) * (ix+1)/6
                za, zb = z0 + (z1-z0) * iz/5, z0 + (z1-z0) * (iz+1)/5
                shade = .60 if (ix + iz) % 2 else .64
                add_box(vertices, faces, colors, (xa, -.08, za), (xb, 0, zb), (shade, shade + .02, shade))
    wall_by_id = {wall['id']: wall for wall in scene['walls']}
    openings_by_wall = {wall['id']: [] for wall in scene['walls']}
    for opening in scene.get('openings', []):
        if opening['wallId'] in openings_by_wall:
            openings_by_wall[opening['wallId']].append(opening)
    wall_color, window_frame, pane, door_frame, door_leaf, sill = (.788,.808,.788), (.384,.478,.502), (.616,.812,.820), (.769,.643,.467), (.663,.541,.384), (.725,.741,.714)
    for wall in scene['walls']:
        length = ((wall['end'][0]-wall['start'][0])**2 + (wall['end'][1]-wall['start'][1])**2) ** .5
        openings = sorted(openings_by_wall[wall['id']], key=lambda opening: opening['offset'])
        cuts = sorted({0, length, *(point for opening in openings for point in (opening['offset'], opening['offset']+opening['width']))})
        for left, right in zip(cuts, cuts[1:]):
            if right-left > 1e-6:
                wall_box(vertices, faces, colors, wall, left, right, 0, wall['height'], wall_color)
        for opening in openings:
            left, right = opening['offset'], opening['offset'] + opening['width']
            bottom, top = opening['bottom'], opening['bottom'] + opening['height']
            if bottom > 1e-6:
                wall_box(vertices, faces, colors, wall, left, right, 0, bottom, wall_color)
            if top < wall['height']:
                wall_box(vertices, faces, colors, wall, left, right, top, wall['height']-top, wall_color)
            trim = min(.04, opening['width']/10, opening['height']/10)
            frame_color = door_frame if opening['type'] == 'door' else window_frame
            for start, end, y, h in ((left, left+trim, bottom, opening['height']), (right-trim, right, bottom, opening['height']),
                                     (left, right, top-trim, trim)):
                wall_box(vertices, faces, colors, wall, start, end, y, h, frame_color, .015)
            if opening['type'] == 'window':
                wall_box(vertices, faces, colors, wall, left, right, bottom, trim, frame_color, .015)
                wall_box(vertices, faces, colors, wall, left+trim, right-trim, bottom+trim, opening['height']-2*trim, pane, -.105)
                wall_box(vertices, faces, colors, wall, left-.04, right+.04, bottom-.015, .03, sill, .06)
            else:
                # Open 90-degree door leaf, matching the blueprint renderer's readable passage.
                (x0, z0), (x1, z1) = wall['start'], wall['end']
                horizontal = abs(x1-x0) >= abs(z1-z0)
                hinge_x, hinge_z = x0 + (x1-x0)*(left+trim)/length, z0 + (z1-z0)*(left+trim)/length
                leaf_width, leaf_height = opening['width']-2*trim, opening['height']-trim-.01
                if horizontal:
                    add_box(vertices, faces, colors, (hinge_x-.02, bottom, hinge_z), (hinge_x+.02, bottom+leaf_height, hinge_z+leaf_width), door_leaf)
                else:
                    add_box(vertices, faces, colors, (hinge_x, bottom, hinge_z-.02), (hinge_x+leaf_width, bottom+leaf_height, hinge_z+.02), door_leaf)
    # Optional furniture is intentionally simple and architectural.  It gives the
    # classroom template readable scale and purpose without claiming recovered
    # photogrammetric geometry.
    for fixture in scene.get('fixtures', []):
        kind = fixture['kind']
        if kind == 'box':
            add_box(vertices, faces, colors, fixture['low'], fixture['high'], fixture['color'])
        elif kind == 'board':
            # The supplied bounds form the dark writing/display surface.  Add a
            # slim warm frame so it reads as a board rather than a flat wall.
            low, high = fixture['low'], fixture['high']
            add_box(vertices, faces, colors, low, high, fixture.get('color', (.08, .16, .10)))
            frame = fixture.get('frameColor', (.30, .23, .16))
            t = .06
            x0, y0, z0 = low; x1, y1, z1 = high
            add_box(vertices, faces, colors, (x0-t, y0-t, z0-t), (x1+t, y0, z1+t), frame)
            add_box(vertices, faces, colors, (x0-t, y1, z0-t), (x1+t, y1+t, z1+t), frame)
            add_box(vertices, faces, colors, (x0-t, y0, z0-t), (x0, y1, z1+t), frame)
            add_box(vertices, faces, colors, (x1, y0, z0-t), (x1+t, y1, z1+t), frame)
    for rows in scene.get('deskRows', []):
        # Each module is a paired student desk and bench: timber tops, gray steel
        # body/legs, and a visible aisle between modules.
        x_start, z_start = rows['start']
        for row in range(rows['count']):
            z = z_start + row * rows['rowSpacing']
            for column in range(rows['columns']):
                x = x_start + column * (rows['deskWidth'] + rows['aisle'])
                width, depth = rows['deskWidth'], rows.get('deskDepth', .48)
                top_y, top_thickness = rows.get('topY', .78), .06
                wood, steel = (.55, .31, .16), (.28, .32, .32)
                add_box(vertices, faces, colors, (x, top_y-top_thickness, z), (x+width, top_y, z+depth), wood)
                # modest steel pedestal beneath the writing surface
                add_box(vertices, faces, colors, (x+.08, .28, z+.06), (x+width-.08, top_y-top_thickness, z+depth-.06), steel)
                # bench immediately behind the desk
                bench_z, bench_y = z + depth + .16, .45
                add_box(vertices, faces, colors, (x, bench_y-.06, bench_z), (x+width, bench_y, bench_z+.30), wood)
                add_box(vertices, faces, colors, (x+.08, .18, bench_z+.05), (x+width-.08, bench_y-.06, bench_z+.25), steel)
    return np.asarray(vertices, float), np.asarray(faces, np.uint32), np.asarray(colors, float)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('video', type=Path)
    parser.add_argument('layout', type=Path)
    parser.add_argument('name')
    args = parser.parse_args()
    scene = json.loads(args.layout.read_text())
    vertices, faces, colors = build(scene)
    target = DEMO_DIR / args.name
    target.mkdir(parents=True, exist_ok=True)
    write_glb(target / 'mesh.glb', vertices, faces, colors, vertex_normals(vertices, faces))
    write_ply(target / 'diagnostic.ply', vertices, faces, colors)
    manifest = {'schemaVersion': '1.0.0', 'kind': 'room-mesh', 'projectId': 'demo', 'jobId': 'bake', 'inputJobId': 'bake',
                'units': 'meters', 'coordinateConvention': 'X right, Y up, Z back; architectural template',
                'worldToViewer': np.eye(4).tolist(), 'calibration': None,
                'artifacts': {'mesh': 'mesh.glb', 'diagnostic': 'diagnostic.ply'}, 'cameras': [], 'inputFrames': [],
                'provenance': {'origin': 'generated', 'source': 'architectural-demo-template', 'completion': 'modeled',
                               'precomputed': True, 'model': None},
                'processing': {'pipeline': 'precomputed-architectural-demo', 'layout': args.layout.name},
                'statistics': {'vertices': len(vertices), 'triangles': len(faces), 'workerSeconds': 0},
                'warnings': ['Precomputed architectural demo model: it is not reconstructed from this video.',
                             'Replace this template with a surveyed plan or a successful photogrammetry capture for real geometry.']}
    save_json(target / 'manifest.json', manifest)
    save_json(target / 'calibration.json', {'rotationDegrees': [0, 0, 0], 'reference': None, 'floor': None})
    digest = hashlib.sha256(args.video.read_bytes()).hexdigest()
    presets_path = DEMO_DIR / 'presets.json'
    presets = json.loads(presets_path.read_text()) if presets_path.is_file() else []
    presets = [preset for preset in presets if preset.get('name') != args.name and preset.get('sha256') != digest]
    presets.append({'name': args.name, 'sha256': digest, 'video': args.video.name})
    save_json(presets_path, presets)
    print(f'{args.name}: {len(vertices)} vertices, {len(faces)} triangles')


if __name__ == '__main__':
    main()
