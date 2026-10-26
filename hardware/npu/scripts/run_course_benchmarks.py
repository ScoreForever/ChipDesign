#!/usr/bin/env python3
"""Course workload benchmark: generality and cycles of the four candidate networks.

Every supported network runs through the real host API on the RTL, with every
tensor write checked against the logical reference (via run_program_regression.simulate).
Networks the compiler cannot lower are reported as unsupported, never skipped silently.
"""
from pathlib import Path
import argparse
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'hardware/npu/scripts'))
sys.path.insert(0, str(ROOT/'software/host'))
from run_program_regression import simulate, run
from npu_program import compile_model, reference
from course_benchmarks import course_models, argmax


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ROOT/'build/course_benchmarks')
    ap.add_argument('--cols', type=int, nargs='+', default=[8, 4])
    ap.add_argument('--trained-model', type=Path, help='use the trained TinyCNN NPZ for KWS (needs NumPy)')
    ap.add_argument('--macro-model', type=Path, help='private course SRAM model; enables both hard macros')
    args = ap.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    sources = (ROOT/'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
    defines = []
    if args.macro_model:
        sources.append(str(args.macro_model.resolve()))
        defines += ['-DNPU_USE_RA1SHD_MODEL', '-DNPU_USE_RA1SHD_ACTIVATION']
    models = list(course_models(args.trained_model))
    rows = []
    for cols in args.cols:
        image = out/f'program_{cols}.vvp'
        ds = defines + (['-DCOLS4', '-DNPU_ARRAY_COLS=4'] if cols == 4 else [])
        run(['iverilog', '-g2012', '-s', 'tb_npu_program', '-o', image, *ds, *sources,
             'hardware/soc/sim/tb/tb_npu_program.sv'], out/f'compile_{cols}.log')
        for name, model, frame in models:
            entry = dict(network=name, cols=cols)
            try:
                pkg = compile_model(model, cols=cols)
            except ValueError as error:
                entry.update(supported=False, reason=str(error)); rows.append(entry)
                print(f'UNSUPPORTED {name} c{cols}: {error}', flush=True); continue
            logits = reference(model, frame)[model['nodes'][-1]['output']]
            row = simulate(out, image, name, model, frame, cols, True, False)
            entry.update(supported=True, cycles=row['cycles'], groups=pkg.manifest['groups'],
                         model_bytes=len(pkg.image), activation_peak=pkg.manifest['activation_peak'],
                         operator_cycles=[op[1]-op[0] for op in row['operator_trace'][0]],
                         predicted_class=argmax(logits), checked_write_events=row['checked_write_events'])
            rows.append(entry)
            print(f'PASS {name} c{cols}: {row["cycles"][0]} cycles', flush=True)
    summary = dict(results=rows)
    for cols in args.cols:
        ok = [r for r in rows if r['cols'] == cols and r['supported']]
        summary[f'geomean_cycles_c{cols}'] = (math.exp(sum(math.log(r['cycles'][0]) for r in ok)/len(ok)) if ok else None)
        summary[f'supported_c{cols}'] = f"{len(ok)}/{len(models)}"
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    lines = ['| Network | Cols | Supported | Cycles | Operators (cycles) |', '|---|---:|---|---:|---|']
    for r in rows:
        ops = ', '.join(f'{"+".join(g)}:{c}' for g, c in zip(r.get('groups', []), r.get('operator_cycles', [])))
        lines.append(f"| {r['network']} | {r['cols']} | {'yes' if r['supported'] else 'no: '+r['reason']} | "
                     f"{r['cycles'][0] if r['supported'] else '—'} | {ops} |")
    for cols in args.cols:
        g = summary[f'geomean_cycles_c{cols}']
        lines.append(f"\nc{cols}: supported {summary[f'supported_c{cols}']}, geomean cycles of supported = "
                     f"{g:.1f}" if g else f"\nc{cols}: none supported")
    (out/'summary.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
