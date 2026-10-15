#!/usr/bin/env python3
"""Fixed RTL-compatible training comparison; select on INT8 validation only.

Train strong baseline, weak candidate and weak+0.05 label-smoothing candidate.
Only the selected candidate and baseline are evaluated on the held-out test.
This script records measured improvement; it never assumes a candidate wins.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]


def run(argv, log):
    with log.open('w') as stream:
        stream.write('$ '+' '.join(map(str, argv))+'\n'); stream.flush()
        subprocess.run(list(map(str, argv)), cwd=ROOT, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--epochs', type=int, default=50)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--parallel', type=int, default=3)
    p.add_argument('--device', default='cuda')
    p.add_argument('--candidate-seeds', type=int, nargs='+', default=[0])
    p.add_argument('--reuse-training-from', type=Path,
                   help='reuse previously completed checkpoints; still re-export/evaluate validation')
    args = p.parse_args()
    out, data = args.output.resolve(), args.data_dir.resolve()
    if out.exists() and any(out.iterdir()): raise ValueError('--output must be new or empty')
    out.mkdir(parents=True, exist_ok=True)
    configs = [('baseline_strong', 'strong', 0.0, 0)]
    for seed in sorted(set(args.candidate_seeds)):
        suffix = '' if seed == 0 else f'_seed{seed}'
        configs += [('candidate_weak'+suffix, 'weak', 0.0, seed),
                    ('candidate_weak_smooth'+suffix, 'weak', 0.05, seed)]
    protocol = {'model': 'TinyCNN-8-Flat', 'labels': ['yes', 'no', 'up', 'down'],
                'epochs': args.epochs, 'evaluation_seed': 0, 'batch_size': 256,
                'calibration_count': 2048, 'criterion': 'INT8 validation macro-F1, then accuracy',
                'test_policy': 'evaluate only baseline and selected candidate after selection',
                'configs': configs, 'reuse_training_from': str(args.reuse_training_from) if args.reuse_training_from else None,
                'source_sha256': {str(path.relative_to(ROOT)):
                    hashlib.sha256(path.read_bytes()).hexdigest() for path in
                    [ROOT/'software/train/train.py', ROOT/'software/train/model.py',
                     ROOT/'software/train/dataset.py', ROOT/'software/tools/export_model.py']}}
    (out/'protocol.json').write_text(json.dumps(protocol, indent=2)+'\n')
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    os.environ.setdefault('MKL_NUM_THREADS', '1')
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')

    def train(config):
        name, aug, smooth, seed = config
        if args.reuse_training_from and (args.reuse_training_from/name/'best.pt').exists():
            previous = json.loads((args.reuse_training_from/'protocol.json').read_text())
            expected = [name, aug, smooth, seed]
            recorded = [list(x) + ([0] if len(x) == 3 else []) for x in previous['configs']]
            if previous['epochs'] != args.epochs or expected not in recorded:
                raise ValueError(f'incompatible checkpoint reuse: {name}')
            shutil.copytree(args.reuse_training_from/name, out/name)
            print('REUSED TRAINING', name, flush=True)
            return
        run([sys.executable, ROOT/'software/train/train.py', '--data-dir', data,
             '--epochs', args.epochs, '--batch-size', 256, '--num-workers', args.workers,
             '--seed', seed, '--device', args.device, '--head', 'flatten', '--task', 'four_class',
             '--augmentation', aug, '--label-smoothing', smooth, '--skip-test', '--output', out/name],
            out/(name+'-train.log'))
        print('TRAIN COMPLETE', name, flush=True)
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        list(pool.map(train, configs))
    validation = {}
    for name, _, _, _ in configs:
        package = out/(name+'.npz')
        run([sys.executable, ROOT/'software/tools/export_model.py', '--checkpoint', out/name/'best.pt',
             '--data-dir', data, '--calib-size', 2048, '--device', args.device, '--output', package],
            out/(name+'-export.log'))
        metrics = out/(name+'-validation.json')
        run([sys.executable, ROOT/'software/tools/evaluate_quantized.py', '--package', package,
             '--data-dir', data, '--split', 'validation', '--json', metrics], out/(name+'-validation.log'))
        validation[name] = json.loads(metrics.read_text())
        print('VALIDATION', name, validation[name]['accuracy'], validation[name]['macro_f1'], flush=True)
    candidates = [name for name, _, _, _ in configs if name != 'baseline_strong']
    selected = max(candidates, key=lambda name: (validation[name]['macro_f1'], validation[name]['accuracy']))
    selection = {'selected_candidate': selected, 'validation': validation}
    (out/'selection.json').write_text(json.dumps(selection, indent=2)+'\n')
    print('SELECTED ON VALIDATION', selected, flush=True)
    test = {}
    for name in ('baseline_strong', selected):
        metrics = out/(name+'-test.json')
        run([sys.executable, ROOT/'software/tools/evaluate_quantized.py', '--package', out/(name+'.npz'),
             '--data-dir', data, '--split', 'test', '--json', metrics], out/(name+'-test.log'))
        test[name] = json.loads(metrics.read_text())
    result = {'protocol': protocol, 'selection': selection, 'test': test,
              'accuracy_delta_percentage_points': 100*(test[selected]['accuracy']-test['baseline_strong']['accuracy']),
              'macro_f1_delta_percentage_points': 100*(test[selected]['macro_f1']-test['baseline_strong']['macro_f1'])}
    (out/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print('TEST ACCURACY DELTA', result['accuracy_delta_percentage_points'], 'percentage points', flush=True)


if __name__ == '__main__': main()
