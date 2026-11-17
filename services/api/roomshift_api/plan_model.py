"""Subprocess boundary for the learned plan model (services/reconstruction/plan_model_worker.py).

The model reads a blueprint into wall, room and symbol class maps that the parser uses as a prior. It is
optional: when the worker, its environment or its (non-commercial) weights are missing, or it fails or runs
out of time, blueprint reconstruction carries on with the drawing rules alone and says so.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np

from .config import plan_model_enabled, plan_model_timeout
from .mesh_worker import WORKER_DIR, terminate_process, worker_python
from .storage import atomic_write_json, read_json

WORKER = 'plan_model_worker.py'


def capability_report() -> dict:
    if not plan_model_enabled():
        return {'ready': False, 'code': 'DISABLED', 'message': 'The learned wall finder is switched off (ROOMSHIFT_PLAN_MODEL=off).'}
    try:
        result = subprocess.run([worker_python(), str(WORKER_DIR / WORKER), '--check'],
                                capture_output=True, text=True, timeout=60)
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'ready': False, 'code': 'WORKER_NOT_CONFIGURED',
                'message': 'The learned wall finder is not installed. See services/reconstruction/README.md.'}


class PlanModelUnavailable(Exception):
    """The model could not be used for this job; the message says why, for the scene's warnings."""


def run_plan_model(image: Path, output: Path, cancelled) -> tuple[dict, dict]:
    """(prior maps, worker manifest) for one blueprint. Raises PlanModelUnavailable, never ApiError:
    a missing model must not fail a reconstruction. `cancelled()` aborts promptly."""
    if not plan_model_enabled():
        raise PlanModelUnavailable('it is switched off (ROOMSHIFT_PLAN_MODEL=off)')
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / 'request.json', {'image': str(image.resolve()), 'parentPid': os.getpid()})
    started = time.monotonic()
    with (output / 'worker.log').open('wb') as log:
        try:
            environment = {**os.environ, 'OMP_NUM_THREADS': os.environ.get('OMP_NUM_THREADS', '4')}
            process = subprocess.Popen([worker_python(), str(WORKER_DIR / WORKER), '--request', str(output / 'request.json')],
                                       stdout=log, stderr=log, env=environment, start_new_session=os.name == 'posix')
        except OSError:
            raise PlanModelUnavailable('the learned wall finder is not installed')
        try:
            while process.poll() is None:
                if cancelled():
                    raise PlanModelUnavailable('cancelled')
                if time.monotonic() - started > plan_model_timeout():
                    raise PlanModelUnavailable('it took longer than ROOMSHIFT_PLAN_MODEL_TIMEOUT')
                time.sleep(0.1)
        finally:
            terminate_process(process)
    if process.returncode != 0:
        error = read_json(output / 'error.json') or {'message': 'the worker stopped unexpectedly (see its log)'}
        raise PlanModelUnavailable(error['message'].rstrip('.'))
    manifest = read_json(output / 'manifest.json')
    try:
        with np.load(output / 'prior.npz') as stored:
            prior = {key: stored[key] for key in ('rooms', 'icons', 'wall')}
    except (OSError, KeyError, ValueError):
        raise PlanModelUnavailable('its output could not be read')
    if manifest is None or any(m.ndim != 2 or m.shape != prior['rooms'].shape for m in prior.values()):
        raise PlanModelUnavailable('its output did not match the image')
    return prior, manifest
