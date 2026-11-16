"""Isolated API for the grouped-project browser test: real reconstruction on an empty temporary data folder."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'services/api'))
import uvicorn
from roomshift_api.config import Settings
from roomshift_api.main import create_app


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='roomshift-assembly-e2e-') as directory:
        settings = replace(Settings.from_env(), data_dir=Path(directory), dev_seed_fixture=False,
                           cors_origins=('http://127.0.0.1:5190',))
        uvicorn.run(create_app(settings), host='127.0.0.1', port=8013, log_level='warning')
