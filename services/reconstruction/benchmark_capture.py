"""Reproducible local API benchmark. Invoke with the API Python, not the GPU Python.

Example from repo root:
services/api/.venv/bin/python services/reconstruction/benchmark_capture.py \
  --photos /path/to/25-photos --output /tmp/room-benchmark --views 12 20 32

Supply real captures for milestone acceptance. Upstream examples are only smoke tests.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'services/api'))
from fastapi.testclient import TestClient
from roomshift_api.config import Settings
from roomshift_api.main import create_app


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--photos', type=Path)
    source.add_argument('--video', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--views', type=int, nargs='+', default=[12], choices=[12, 20, 32, 40])
    parser.add_argument('--label', default='local capture; quality not independently assessed')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    settings = replace(Settings.from_env(), data_dir=args.output / 'data', dev_seed_fixture=False)
    files = [args.video] if args.video else sorted(p for p in args.photos.iterdir() if p.suffix.lower() in {'.png', '.jpg', '.jpeg'})
    kind = 'video' if args.video else 'photo-set'
    report = {'label': args.label, 'source': str((args.video or args.photos).resolve()), 'sourceKind': kind, 'runs': []}
    def persist():
        (args.output/'report.json').write_text(json.dumps(report, indent=2))
    def wait(client, job):
        last = None
        deadline = time.monotonic()+400
        while job['status'] in {'queued', 'running'}:
            if time.monotonic() > deadline:
                client.post(f"/api/jobs/{job['id']}/cancel")
                raise TimeoutError('Benchmark polling deadline exceeded')
            state = (job.get('stage'), job['status'])
            if state != last:
                print(job['id'], *state, flush=True)
                last = state
            time.sleep(.5)
            job = client.get(f"/api/jobs/{job['id']}").json()['job']
        return job
    with TestClient(create_app(settings)) as client:
        started = time.monotonic()
        uploaded = client.post('/api/captures', data={'kind': kind, 'name': 'Benchmark capture'},
                               files=[('files', (p.name, p.read_bytes(), 'video/mp4' if args.video else 'image/png' if p.suffix.lower()=='.png' else 'image/jpeg')) for p in files])
        if uploaded.status_code != 201:
            report['uploadError'] = uploaded.json(); persist(); return 1
        body = uploaded.json()
        prepared = wait(client, body['job'])
        report['preparationJob'] = prepared
        report['acceptedInputToPreparedSeconds'] = time.monotonic()-started
        if prepared['status'] != 'succeeded':
            persist(); print(json.dumps(prepared.get('error')), flush=True); return 1
        pid = body['project']['id']
        for count in args.views:
            started = time.monotonic()
            submitted = client.post(f'/api/projects/{pid}/reconstruct-mesh', json={'maxViews': count})
            if submitted.status_code != 202:
                report['runs'].append({'requestedViews': count, 'submissionError': submitted.json()})
                persist()
                return 1
            job = wait(client, submitted.json()['job'])
            run = {'requestedViews': count, 'job': job, 'observedSeconds': time.monotonic()-started}
            run['acceptedInputToMeshSeconds'] = report['acceptedInputToPreparedSeconds'] + run['observedSeconds']
            if job['status'] == 'succeeded':
                run['manifest'] = client.get(job['meshManifestUrl']).json()
            report['runs'].append(run); persist()
            print(json.dumps({'views': count, 'status': job['status'], 'error': job.get('error')}), flush=True)
    return 0 if all(r['job']['status']=='succeeded' for r in report['runs']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
