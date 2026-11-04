#!/usr/bin/env python3
"""Rebuild test_frames.pt for packaged models from the public datasets, so the
full test set can be scored without retraining.

Uses the same loader, input scale and channel padding as train.py, so the
frames are identical to the ones written at training time.

  python software/zoo/make_test_frames.py --data datasets --models software/zoo/pretrained
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'software/host'), str(ROOT / 'software'), str(ROOT / 'software/zoo')]
import torch  # noqa: E402
from zoo import data as datasets  # noqa: E402
from zoo.hwopt import frames_for  # noqa: E402
from train import quantize_input  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', type=Path, required=True)
    ap.add_argument('--models', type=Path, required=True)
    args = ap.parse_args()
    for run in sorted(p for p in args.models.iterdir() if (p / 'model.json').exists()):
        model = json.loads((run / 'model.json').read_text())
        raw = datasets.LOADERS[run.name](args.data)
        x, y = raw['test']
        frames = frames_for(model, quantize_input(x.cpu(), model['metadata']['input_scale'])).to(torch.int8)
        golden = run / 'golden.json'
        if golden.exists():
            first = json.loads(golden.read_text())['frames']
            assert all(frames[r['index']].flatten().tolist() == r['input'] for r in first), \
                f'{run.name}: rebuilt frames differ from golden.json (dataset or preprocessing mismatch)'
        torch.save(dict(frames=frames, labels=y.cpu().to(torch.int16)), run / 'test_frames.pt')
        print(run.name, 'test frames', len(y), 'match golden' if golden.exists() else '')


if __name__ == '__main__':
    main()
