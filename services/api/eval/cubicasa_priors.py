"""Run the learned plan model once over CubiCasa5K plans and cache its maps, so the parser can be tuned
without the model. Run with the reconstruction environment (it has torch), from services/api:

    ../reconstruction/.venv/bin/python -m eval.cubicasa_priors --split test --n 100

Writes .baseline/priors/<split>/<sample>.npz (rooms, icons, wall), the same arrays the worker produces.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from .cubicasa_data import ROOT, samples

PRIORS = ROOT.parents[1] / "priors"


def prior_path(split: str, name: str) -> Path:
    return PRIORS / split / f"{name}.npz"


def load_prior(split: str, name: str) -> dict | None:
    path = prior_path(split, name)
    if not path.is_file():
        return None
    with np.load(path) as stored:
        return {key: stored[key] for key in stored.files}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--start", type=int, default=0)
    args = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "reconstruction"))
    import plan_model_worker as worker
    import torch
    torch.set_num_threads(8)
    model = worker.load_model(torch)
    for folder in samples(args.split)[args.start:args.start + args.n]:
        target = prior_path(args.split, folder.name)
        if target.is_file():
            continue
        gray = cv2.imread(str(folder / "F1_scaled.png"), cv2.IMREAD_GRAYSCALE)
        rooms, icons, wall = worker.predict(gray, model, torch)
        target.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(target, rooms=rooms, icons=icons, wall=wall)
        print(folder.name, flush=True)


if __name__ == "__main__":
    main()
