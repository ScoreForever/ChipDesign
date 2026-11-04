#!/usr/bin/env python3
"""Score exported zoo models on their full test sets with the hardware-exact
integer reference (npu_fastref, current ABI semantics) and check capacity.

  python software/zoo/evaluate.py --runs zoo_runs/pick --output results.json
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'software/host'), str(ROOT / 'software')]
import torch  # noqa: E402
import npu_fastref  # noqa: E402
from npu_program import compile_model  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runs', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    rows = []
    for run in sorted(p for p in args.runs.iterdir() if (p / 'model.json').exists()):
        model = json.loads((run / 'model.json').read_text())
        blob = torch.load(run / 'test_frames.pt')
        x, y = blob['frames'].long(), blob['labels'].long()
        preds = []
        for i in range(0, len(y), 2048):
            out = npu_fastref.run(model, x[i:i + 2048].to(device), device)
            preds.append(out.reshape(out.shape[0], -1).argmax(1).cpu())
        pred = torch.cat(preds)
        classes = int(y.max()) + 1
        recall = [((pred == c) & (y == c)).sum().item() / max(1, (y == c).sum().item()) for c in range(classes)]
        train = json.loads(next(p for p in (run / 'result.json', run / 'training_result.json') if p.exists()).read_text())
        hw = {}
        for cols in (8, 4):
            pkg = compile_model(model, cols=cols)
            m = pkg.manifest
            hw[f'c{cols}'] = dict(model_bytes=m['model_bytes'], activation_peak=m['activation_peak'], descriptors=m['op_count'],
                                  groups=m['groups'])
        weights = sum(len(sum(n['weights'], [])) if isinstance(n['weights'][0], list) else len(n['weights'])
                      for n in model['nodes'] if n['op'] in ('Conv', 'FC'))
        rows.append(dict(model=run.name, run=str(run.resolve()), test_samples=len(y), int4_test=(pred == y).float().mean().item(),
                         int4_test_balanced=sum(recall) / len(recall), recall=recall,
                         float_test=train.get('float_test'), int4_val_at_selection=train.get('int4_val'),
                         weights=weights, hardware=hw, metadata={k: v for k, v in model['metadata'].items() if k != 'classes'}))
        print(f"{run.name:12s} int4 test {rows[-1]['int4_test']:.4f} (balanced {rows[-1]['int4_test_balanced']:.4f}) "
              f"float {train.get('float_test')} model {hw['c8']['model_bytes']} B act {hw['c8']['activation_peak']} B")
    args.output.write_text(json.dumps(rows, indent=1) + '\n')


if __name__ == '__main__':
    main()
