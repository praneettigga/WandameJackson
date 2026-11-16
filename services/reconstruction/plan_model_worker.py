"""One-shot learned floor-plan model: a plan image in, per-pixel wall, room and symbol classes out.

The CubiCasa5K multi-task network (Kalervo et al. 2019) segments a drawing into room classes (one of them
"Wall") and symbol classes (windows, doors, closets, toilets, sinks, baths, ...). The blueprint parser uses
these maps as a prior: they say where the walls are, so hatching, furniture outlines and dimension lines can
be left out before the parser's own wall, door and room stages run.

Code and weights are CC BY-NC 4.0 (non-commercial use only) and are not bundled; see README.md.
Runs on the CPU in its own process, like the depth worker, so the API never imports torch.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path

VERSION = '1.0.0'
ROOT = Path(__file__).resolve().parents[2]
REPO = Path(os.environ.get('ROOMSHIFT_PLAN_MODEL_REPO', ROOT / 'services/api/.baseline/cubicasa5k'))
WEIGHTS = Path(os.environ.get('ROOMSHIFT_PLAN_MODEL_WEIGHTS', ROOT / 'services/api/.baseline/weights/model_best_val_loss_var.pkl'))
LICENSE = 'CC-BY-NC-4.0'
MODEL = 'CubiCasa5K hg_furukawa_original (model_best_val_loss_var)'
# The network was trained on plans of roughly this size; larger drawings are read downscaled.
MAX_SIDE = int(os.environ.get('ROOMSHIFT_PLAN_MODEL_MAX_SIDE', '1000'))
ROOMS = ['background', 'outdoor', 'wall', 'kitchen', 'living', 'bedroom', 'bath', 'entry', 'railing', 'storage',
         'garage', 'undefined']
ICONS = ['none', 'window', 'door', 'closet', 'appliance', 'toilet', 'sink', 'sauna-bench', 'fireplace', 'bathtub',
         'chimney']


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, allow_nan=False))
    temporary.replace(path)


def capabilities():
    missing = [name for name in ('torch', 'scipy', 'skimage', 'shapely', 'cv2') if importlib.util.find_spec(name) is None]
    if missing:
        return {'ready': False, 'code': 'WORKER_NOT_CONFIGURED', 'missing': missing,
                'message': 'Install the plan model dependencies. See services/reconstruction/README.md.'}
    if not (REPO / 'floortrans/models/hg_furukawa_original.py').is_file() or not WEIGHTS.is_file():
        return {'ready': False, 'code': 'MODEL_NOT_INSTALLED',
                'message': 'Download the CubiCasa5K code and weights (non-commercial). See services/reconstruction/README.md.'}
    return {'ready': True, 'code': 'READY', 'model': MODEL, 'license': LICENSE, 'workerVersion': VERSION,
            'message': 'Learned wall finder ready (CPU, non-commercial weights).'}


def load_model(torch):
    sys.path.insert(0, str(REPO))
    from floortrans.models.hg_furukawa_original import hg_furukawa_original
    model = hg_furukawa_original(n_classes=51)
    model.conv4_ = torch.nn.Conv2d(256, 44, bias=True, kernel_size=1)
    model.upsample = torch.nn.ConvTranspose2d(44, 44, kernel_size=4, stride=4)
    # weights_only=True refuses to execute code embedded in the pickle.
    model.load_state_dict(torch.load(WEIGHTS, map_location='cpu', weights_only=True)['model_state'])
    return model.eval()


def predict(gray, model, torch, max_side=MAX_SIDE):
    """(room class map, symbol class map, wall probability) at the image's own size."""
    import cv2
    import numpy as np
    import torch.nn.functional as F
    h, w = gray.shape
    factor = min(1.0, max_side / max(h, w))
    small = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA) if factor < 1 else gray
    rgb = np.repeat(small[:, :, None], 3, axis=2).astype(np.float32)
    x = torch.from_numpy(2 * (rgb / 255.0) - 1).permute(2, 0, 1)[None]
    with torch.inference_mode():
        out = F.interpolate(model(x), size=(h, w), mode='bilinear', align_corners=False)[0]
    rooms = torch.softmax(out[21:33], 0)
    icons = torch.softmax(out[33:44], 0)
    return (rooms.argmax(0).numpy().astype(np.uint8), icons.argmax(0).numpy().astype(np.uint8),
            (rooms[ROOMS.index('wall')].numpy() * 255).astype(np.uint8))


def run(request, output):
    import cv2
    import numpy as np
    import torch
    started = time.monotonic()
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    save_json(output / 'progress.json', {'progress': 0.1, 'stage': 'loading_model'})
    image = Path(request['image'])
    gray = cv2.imdecode(np.fromfile(image, np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError('The plan image could not be read.')
    model = load_model(torch)
    save_json(output / 'progress.json', {'progress': 0.4, 'stage': 'reading_plan'})
    rooms, icons, wall = predict(gray, model, torch, int(request.get('maxSide', MAX_SIDE)))
    np.savez_compressed(output / 'prior.npz', rooms=rooms, icons=icons, wall=wall)
    save_json(output / 'manifest.json', {
        'workerVersion': VERSION, 'model': MODEL, 'license': LICENSE,
        'weightsSha256': hashlib.sha256(WEIGHTS.read_bytes()).hexdigest(),
        'imageSize': [int(gray.shape[1]), int(gray.shape[0])], 'rooms': ROOMS, 'icons': ICONS,
        'seconds': time.monotonic() - started})
    save_json(output / 'progress.json', {'progress': 1.0, 'stage': 'complete'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--request', type=Path)
    args = parser.parse_args()
    output = args.request.parent if args.request else None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            status = capabilities()
        if args.check:
            print(json.dumps(status))
            return 0
        if not status['ready']:
            save_json(output / 'error.json', status)
            return 1
        with contextlib.redirect_stdout(sys.stderr):
            run(json.loads(args.request.read_text()), output)
        return 0
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        code, message = 'PLAN_MODEL_FAILED', str(error)
        if 'out of memory' in message.lower() or isinstance(error, MemoryError):
            code, message = 'OUT_OF_MEMORY', 'Memory was exhausted reading the plan with the learned model.'
        if output:
            save_json(output / 'error.json', {'code': code, 'message': message})
        else:
            print(json.dumps({'ready': False, 'code': code, 'message': message}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
