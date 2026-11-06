#!/usr/bin/env python3
"""Package selected zoo runs as repository artifacts (software/zoo/pretrained).

For each run directory (model.json, qat_state.pt, result.json, train.log,
test_frames.pt) this writes:

  model.json            exported INT4 model (weights, per-channel Q15 params)
  qat_state.pt          QAT checkpoint (float weights + learned scales)
  training_result.json  training-time metrics and the exact train.py arguments
  train.log             training log
  golden.json           N test frames evenly spaced over the test set (some
                        test sets are sorted by class): input codes, label, the integer
                        reference output, and the exact input/output bytes the
                        host writes and reads (for RTL replays and board tests)
  c8/              compiled NPU images (model.bin + manifest.json, fused)

and a top-level MANIFEST.json with SHA-256 of every file and the full-test-set
INT4 accuracy from docs/validation/int4_v2/zoo_eval.json.

  python software/zoo/package_models.py --runs zoo_runs/pick --output software/zoo/pretrained
"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'software/host'), str(ROOT / 'hardware/npu/scripts')]
import torch  # noqa: E402
from npu_program import compile_model, reference  # noqa: E402
from run_program_regression import encoded  # noqa: E402


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runs', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--frames', type=int, default=16)
    args = ap.parse_args()
    evals = {r['model']: r for r in json.loads((ROOT / 'docs/validation/int4_v2/zoo_eval.json').read_text())}
    manifest = {}
    for run in sorted(p for p in args.runs.iterdir() if (p / 'model.json').exists()):
        name = run.name
        out = args.output / name
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        for src, dst in (('model.json', 'model.json'), ('qat_state.pt', 'qat_state.pt'),
                         ('result.json', 'training_result.json'), ('train.log', 'train.log')):
            shutil.copyfile(run / src, out / dst)
        model = json.loads((run / 'model.json').read_text())
        for cols in (8,):
            compile_model(model, cols=cols, fuse=True).save(out / f'c{cols}')
        blob = torch.load(run / 'test_frames.pt')
        package = compile_model(model, cols=8, fuse=True)
        in_dtype = model['input'].get('dtype', 'i4')
        out_dtype = package.manifest['output_dtype']
        rows = []
        total = len(blob['labels'])
        for index in sorted({i * total // args.frames for i in range(args.frames)}):
            frame, label = blob['frames'][index], blob['labels'][index]
            values = frame.flatten().tolist()
            logits = reference(model, values)[model['nodes'][-1]['output']]
            rows.append(dict(index=index, label=int(label), predicted=max(range(len(logits)), key=logits.__getitem__),
                             input=values, output=list(logits),
                             input_hex=bytes(encoded(values, in_dtype)).hex(),
                             output_hex=bytes(encoded(logits, out_dtype)).hex()))
        golden = dict(model=name, input_shape=model['input']['shape'], input_dtype=in_dtype, output_dtype=out_dtype,
                      input_scale=model['metadata'].get('input_scale'), preprocess=model['metadata'].get('preprocess'),
                      note='test-set frames at the given indices; output is the bit-exact integer reference (npu_program.reference)',
                      frames=rows)
        (out / 'golden.json').write_text(json.dumps(golden) + '\n')
        ev = evals.get(name, {})
        manifest[name] = dict(
            int4_test=ev.get('int4_test'), int4_test_balanced=ev.get('int4_test_balanced'),
            float_test=ev.get('float_test'), test_samples=ev.get('test_samples'),
            model_bytes_c8=package.manifest['model_bytes'],
            train_args=json.loads((run / 'result.json').read_text()).get('args'),
            golden_frames=len(rows), golden_correct=sum(r['label'] == r['predicted'] for r in rows),
            sha256={str(p.relative_to(out)): sha256(p) for p in sorted(out.rglob('*')) if p.is_file()})
        print(name, 'golden', manifest[name]['golden_correct'], '/', len(rows), 'bytes', package.manifest['model_bytes'])
    (args.output / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1) + '\n')


if __name__ == '__main__':
    main()
