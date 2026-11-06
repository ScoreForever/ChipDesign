#!/usr/bin/env python3
"""Check packaged models against the current compiler and integer reference.

For every software/zoo/pretrained/<model>: the compiled c8 images must be
byte-identical to the packaged ones, and the integer reference must reproduce
each golden frame's output and output bytes. Needs no dataset, GPU or torch.

  python software/zoo/check_golden.py [--models software/zoo/pretrained]
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'software/host'), str(ROOT / 'hardware/npu/scripts')]
from npu_program import compile_model, reference  # noqa: E402
from run_program_regression import encoded  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--models', type=Path, default=ROOT / 'software/zoo/pretrained')
    args = ap.parse_args()
    failures = 0
    for run in sorted(p for p in args.models.iterdir() if (p / 'model.json').exists()):
        model = json.loads((run / 'model.json').read_text())
        problems = []
        for cols in (8,):
            pkg = compile_model(model, cols=cols, fuse=True)
            if pkg.image != (run / f'c{cols}/model.bin').read_bytes():
                problems.append(f'c{cols} image differs')
        golden = json.loads((run / 'golden.json').read_text())
        for i, row in enumerate(golden['frames']):
            out = reference(model, row['input'])[model['nodes'][-1]['output']]
            if list(out) != row['output'] or bytes(encoded(out, golden['output_dtype'])).hex() != row['output_hex']:
                problems.append(f'frame {i} output differs')
        correct = sum(r['label'] == r['predicted'] for r in golden['frames'])
        print(f"{run.name:12s} {'OK' if not problems else 'FAIL'}  frames {len(golden['frames'])}, "
              f"label match {correct}/{len(golden['frames'])}", *problems)
        failures += bool(problems)
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
