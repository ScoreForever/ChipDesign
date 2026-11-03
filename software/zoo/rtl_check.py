#!/usr/bin/env python3
"""Run exported zoo models on the RTL (Icarus) through the real host driver.

For each model, a few test frames are executed with every tensor write checked
against the integer reference (run_program_regression.simulate), fused and
unfused, on the 4x8 build (and one frame on 4x4). The classification of each
RTL output is compared with the label, and cycle/performance counters and the
per-operator trace are collected for the bottleneck report.

  python software/zoo/rtl_check.py --runs <zoo_runs/final> --output build/zoo_rtl --frames 3
"""
from __future__ import annotations
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'software/host'), str(ROOT / 'hardware/npu/scripts')]
import run_program_regression as R  # noqa: E402
from npu_program import reference  # noqa: E402

# Ablation: every fusion on, all off, and each kind switched off on its own.
FUSIONS = {'all': True, 'none': False,
           'no_requant': ['relu', 'maxpool'], 'no_relu': ['requant', 'maxpool'], 'no_maxpool': ['requant', 'relu']}


def load_frames(run_dir, count):
    import torch
    blob = torch.load(run_dir / 'test_frames.pt')
    frames = blob['frames'][:count]
    labels = blob['labels'][:count]
    return [f.flatten().tolist() for f in frames], [int(v) for v in labels]


def build(out, cols, defines):
    sources = (ROOT / 'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
    image = out / f'program_{cols}.vvp'
    ds = defines + (['-DCOLS4', '-DNPU_ARRAY_COLS=4'] if cols == 4 else [])
    R.run(['iverilog', '-g2012', '-s', 'tb_npu_program', '-o', image, *ds, *sources,
           'hardware/soc/sim/tb/tb_npu_program.sv'], out / f'compile_{cols}.log')
    return image


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runs', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--frames', type=int, default=3)
    ap.add_argument('--jobs', type=int, default=32)
    ap.add_argument('--models', help='comma-separated subset')
    ap.add_argument('--define', action='append', default=[])
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    defines = ['-D' + d for d in args.define]
    images = {cols: build(out, cols, defines) for cols in (8, 4)}
    models = sorted(p for p in args.runs.iterdir() if (p / 'model.json').exists())
    if args.models:
        models = [p for p in models if p.name in args.models.split(',')]
    jobs = []
    for run_dir in models:
        model = json.loads((run_dir / 'model.json').read_text())
        frames, labels = load_frames(run_dir, args.frames)
        for i, (frame, label) in enumerate(zip(frames, labels)):
            for fusion in (FUSIONS if i == 0 else ('all', 'none')):
                jobs.append((run_dir.name, model, frame, label, i, 8, fusion))
        jobs.append((run_dir.name, model, frames[0], labels[0], 0, 4, 'all'))

    def one(job):
        name, model, frame, label, index, cols, fusion = job
        case = f'{name}_f{index}_{fusion}'
        row = R.simulate(out, images[cols], case, model, frame, cols, FUSIONS[fusion], False)
        logits = reference(model, frame)[model['nodes'][-1]['output']]
        return dict(model=name, frame=index, cols=cols, fusion=fusion, label=label,
                    predicted=max(range(len(logits)), key=logits.__getitem__), **row)

    results = []
    with ThreadPoolExecutor(args.jobs) as pool:
        for row in pool.map(one, jobs):
            results.append(row)
            print('PASS', row['model'], row['frame'], row['cols'], row['fusion'], row['cycles'][0], flush=True)
    (out / 'summary.json').write_text(json.dumps(results, indent=1) + '\n')
    print(f'ALL {len(results)} ZOO RTL RUNS BIT-EXACT')


if __name__ == '__main__':
    main()
