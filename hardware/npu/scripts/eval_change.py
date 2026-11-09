#!/usr/bin/env python3
"""Evaluate one RTL/compiler state and export dashboard tables.

  run     execute every suite against THIS checkout's sources into build/eval/<label>/
  export  normalise a run directory into CSVs (models, layers, limits, fuzz, deploy, runs)

Suites (all check every tensor write against the software reference):
  regression  run_program_regression.py (correctness gate)
  limits      profile_bottlenecks.py --limits-only (K, reuse, OC, post, descriptors, capacity)
  ext         profile_limits.py (gather IC, kernel/stride, depthwise, elementwise, fusion, spatial)
  fuzz        profile_fuzz.py (seeded random models across the ABI envelope)
  models      seven packaged models, FPGA (CHIPDESIGN_FPGA_MEM) and ASIC memory modes, per-op trace
  deploy      deploy_envelope.py (compile-only capacity envelope)

A run is a candidate measurement, not an implementation signoff: no synthesis,
STA, place-and-route or board test is part of it.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / 'hardware/npu/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / 'software/host'))

FUZZ_SEED, FUZZ_COUNT = 20261109, 80
OP_FIELDS = ('start', 'end', 'descriptor', 'flags', 'wait_activation', 'wait_weight', 'wait_slot', 'mac_issues')


def sources_digest():
    files = (ROOT / 'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().split()
    files += ['software/host/npu_program.py', 'hardware/soc/sim/tb/tb_npu_program.sv']
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode() + b'\0' + (ROOT / f).read_bytes())
    return h.hexdigest()


def git(*args):
    try:
        return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return 'UNKNOWN'


def sh(cmd, log):
    with open(log, 'w') as f:
        p = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    return p.returncode


def run_models(out, defines, jobs):
    import profile_bottlenecks as pb
    import run_program_regression as rg
    out.mkdir(parents=True)
    cases = [c for c in pb.workloads() if c[0].startswith('packaged_')]
    srcs = (ROOT / 'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().split()
    img = out / 'program.vvp'
    rg.run(['iverilog', '-g2012', '-s', 'tb_npu_program', '-o', img,
            *['-D' + d for d in ['SYNTHESIS', 'NPU_ARRAY_COLS=8', 'NPU_ENABLE_WAVE_TRACE=0',
                                  'NPU_ENABLE_OP_TRACE=1', 'NPU_TILE=16', *defines]],
            *srcs, 'hardware/soc/sim/tb/tb_npu_program.sv'], out / 'compile.log')

    def one(c):
        n, model, frame, fused = c
        row = rg.simulate(out, img, n, model, frame, 8, fused, False, True)
        row['metrics'] = pb.annotate(row, model, frame)
        print('PASS', n, row['cycles'], flush=True)
        return row
    with ThreadPoolExecutor(jobs) as p:
        rows = list(p.map(one, cases))
    (out / 'summary.json').write_text(json.dumps(dict(tests=rows), indent=1) + '\n')


def cmd_run(args):
    out = ROOT / 'build/eval' / args.label
    out.mkdir(parents=True, exist_ok=False)
    py = sys.executable
    meta = dict(label=args.label, description=args.description, role=args.role,
                git_head=git('rev-parse', 'HEAD'), git_branch=git('rev-parse', '--abbrev-ref', 'HEAD'),
                dirty=bool(git('status', '--porcelain')), sources_sha256=sources_digest(),
                started=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'))
    status = {}
    suites = args.suites
    rel = out.relative_to(ROOT)
    if 'regression' in suites:
        status['regression'] = sh([py, 'hardware/npu/scripts/run_program_regression.py', '--jobs', str(args.jobs),
                                   '--output', str(rel / 'regression')], out / 'regression.log')
    if 'limits' in suites:
        status['limits'] = sh([py, 'hardware/npu/scripts/profile_bottlenecks.py', '--limits-only', '--tiles', '16',
                               '--jobs', str(args.jobs), '--output', str(rel / 'limits')], out / 'limits.log')
    if 'ext' in suites:
        status['ext'] = sh([py, 'hardware/npu/scripts/profile_limits.py', '--jobs', str(args.jobs),
                            '--output', str(rel / 'ext')], out / 'ext.log')
    if 'fuzz' in suites:
        status['fuzz'] = sh([py, 'hardware/npu/scripts/profile_fuzz.py', '--count', str(FUZZ_COUNT), '--seed', str(FUZZ_SEED),
                             '--jobs', str(args.jobs), '--output', str(rel / 'fuzz')], out / 'fuzz.log')
    if 'models' in suites:
        for mode, defs in (('fpga', ['CHIPDESIGN_FPGA_MEM']), ('asic', [])):
            try:
                run_models(out / f'models_{mode}', defs, args.jobs)
                status[f'models_{mode}'] = 0
            except Exception as e:
                (out / f'models_{mode}.error').write_text(str(e))
                status[f'models_{mode}'] = 1
    if 'deploy' in suites:
        status['deploy'] = sh([py, 'hardware/npu/scripts/deploy_envelope.py', '--output', str(rel / 'deploy.json')],
                              out / 'deploy.log')
    meta.update(status=status, finished=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'))
    (out / 'meta.json').write_text(json.dumps(meta, indent=1) + '\n')
    print(json.dumps(meta, indent=1))
    return 0 if all(v == 0 for v in status.values()) else 1


# ----------------------------------------------------------------- export

def layer_class(node):
    op = node['op']
    if op != 'Conv':
        return op
    k = node.get('kernel', 3)
    kk = k * k if isinstance(k, int) else k[0] * k[1]
    return 'depthwise' if node.get('groups') else ('pointwise' if kk == 1 else 'conv_kxk')


def model_layers(name, log_text):
    import npu_program as npc
    model = json.loads((ROOT / f'software/zoo/pretrained/{name}/model.json').read_text())
    pkg = npc.compile_model(model, cols=8)
    vals = [int(v) for v in re.findall(r'OPTRACE reg=[0-9a-f]+ value=(\d+)', log_text)]
    it = iter(model['nodes'])
    heads = []
    for g in pkg.manifest['groups']:
        heads.append(next(it))
        for _ in g[1:]:
            next(it)
    rows = []
    for pc in range(pkg.manifest['op_count']):
        r = dict(zip(OP_FIELDS, vals[pc * 8:pc * 8 + 8]))
        rows.append(dict(model=name, pc=pc, layer_class=layer_class(heads[pc]),
                         fused='+'.join(pkg.manifest['groups'][pc]), cycles=r['end'] - r['start'],
                         mac_issues=r['mac_issues'], wait_activation=r['wait_activation'],
                         wait_weight=r['wait_weight'], wait_slot=r['wait_slot']))
    return rows


COUNTER_COLS = ['active_cycles', 'mac_issues', 'mac_wait_activation_cycles', 'mac_wait_weight_cycles',
                'mac_wait_slot_cycles', 'kernel_prepare_cycles', 'other_cycles', 'model_requests',
                'activation_requests', 'writes', 'post_lanes', 'useful_macs', 'useful_array_util_pct']

LIMIT_AXES = [  # (regex on case, axis name, x label)
    (r'^limit_k(\d+)_', 'k_depth', 'K (one position)'),
    (r'^limit_reuse(\d+)_', 'reuse', 'positions (K=8)'),
    (r'^limit_oc(\d+)_', 'out_channels', 'output channels (K=8, 16 positions)'),
    (r'^limit_compute_k(\d+)_p(\d+)', 'compute', 'K x positions'),
    (r'^limit_post_k(\d+)_q(\d)', 'post', 'K, quantized'),
    (r'^gather_ic(\d+)_', 'gather_ic', 'input channels (3x3, 8x8x8)'),
    (r'^kernel(\d+)_s(\d)', 'kernel', 'kernel size'),
    (r'^dw_c(\d+)_', 'depthwise', 'channels (3x3 depthwise)'),
    (r'^(relu|maxpool2|avgpool3|add)_c(\d+)', 'elementwise', 'operator, channels'),
    (r'^conv_pool_c8_f(\d)', 'fusion', 'fused'),
    (r'^spatial32_oc(\d+)', 'spatial', 'output channels (32x32x8 3x3)'),
    (r'^(descriptors_\d+|full_activation\w*|max_k_int4)', 'capacity', 'case'),
]


def limit_row(suite, t):
    case = t['case']
    m = t['metrics']
    row = dict(suite=suite, case=case, axis='other', x='', x2='')
    for pat, axis, _ in LIMIT_AXES:
        g = re.match(pat, case)
        if g:
            row.update(axis=axis, x=g.group(1), x2=g.group(2) if g.lastindex and g.lastindex > 1 else '')
            break
    row.update({k: m.get(k, '') for k in COUNTER_COLS})
    row['cycles_per_issue'] = round(m['active_cycles'] / m['mac_issues'], 3) if m['mac_issues'] else ''
    return row


def ext_bounds(case, fused):
    import profile_limits as pl
    for name, model, frame, fu in pl.workloads():
        if case.startswith(name + '_c8_') and fu == fused:
            return pl.bounds(model, fused)
    return {}


def cmd_export(args):
    src = (ROOT / 'build/eval' / args.label) if not args.run_dir else Path(args.run_dir).resolve()
    out = Path(args.output).resolve() if args.output else src / 'export'
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((src / 'meta.json').read_text()) if (src / 'meta.json').exists() else dict(label=args.label)
    label = args.label
    tables = {k: [] for k in ('models', 'layers', 'limits', 'fuzz', 'deploy')}
    for mode in ('fpga', 'asic'):
        s = src / f'models_{mode}' / 'summary.json'
        if not s.exists():
            continue
        for t in json.loads(s.read_text())['tests']:
            name = t['case'].replace('packaged_', '').replace('_c8_f1_j0', '')
            tables['models'].append(dict(label=label, mode=mode, model=name, cycles_warm=t['cycles'][1],
                                         **{k: t['metrics'][k] for k in COUNTER_COLS}))
            log = (src / f'models_{mode}' / t['case'] / 'rtl.log').read_text()
            tables['layers'] += [dict(label=label, mode=mode, **r) for r in model_layers(name, log)]
    for suite in ('limits', 'ext'):
        s = src / suite / 'summary.json'
        if not s.exists():
            continue
        for t in json.loads(s.read_text())['tests']:
            row = limit_row(suite, t)
            if suite == 'ext':
                b = ext_bounds(t['case'], t['fused'])
                row['bound_cycles'] = b.get('bound_max', '')
            else:
                row['bound_cycles'] = ''
            tables['limits'].append(dict(label=label, **row))
    s = src / 'fuzz' / 'summary.json'
    fuzz_fail = 0
    if s.exists():
        f = json.loads(s.read_text())
        for t in f['tests']:
            if 'failed' in t:
                fuzz_fail += 1
                tables['fuzz'].append(dict(label=label, case=t['case'], family=t['family'], tags='+'.join(t['tags']),
                                           status='FAIL', fused=t['fused']))
                continue
            m = t['metrics']
            tables['fuzz'].append(dict(label=label, case=t['case'], family=t['family'], tags='+'.join(t['tags']),
                                       status='PASS', fused=t['fused'], ops=t['ops'], cycles=m['active_cycles'],
                                       mac_issues=m['mac_issues'], port_requests=m['port_requests'],
                                       bound_cycles=m['bound_max'], bound_efficiency_pct=round(m['bound_efficiency_pct'], 2),
                                       useful_array_util_pct=round(m['useful_array_util_pct'], 3)))
        meta['fuzz_rejected'] = len(f['rejected'])
    s = src / 'deploy.json'
    if s.exists():
        for r in json.loads(s.read_text()):
            cfg = ', '.join(f'{k}={r[k]}' for k in ('alpha', 'res', 'k', 'length') if k in r)
            tables['deploy'].append(dict(label=label, family=r['family'], config=cfg, params=r['params'],
                                         layers=r['layers'], fits=r['fits'], model_bytes=r.get('model_bytes', ''),
                                         activation_peak=r.get('activation_peak', ''),
                                         constraint='' if r['fits'] else r['constraint'],
                                         reason='' if r['fits'] else r['reason']))
    reg = src / 'regression.log'
    reg_pass = reg_total = ''
    if reg.exists():
        text = reg.read_text()
        # Count individual checks (program runs + reject/lifecycle checks) so pass/total share a unit;
        # total is INCOMPLETE unless the runner printed its final all-passed line.
        reg_pass = len(re.findall(r'^PASS', text, re.M))
        reg_fail = len(re.findall(r'^FAIL', text, re.M))
        done = re.search(r'ALL \d+ PROGRAM TESTS PASSED', text)
        reg_total = reg_pass + reg_fail if done or reg_fail else 'INCOMPLETE'
    count = lambda name: len(tables[name])
    run = dict(label=label, role=meta.get('role', ''), description=meta.get('description', ''),
               git_head=meta.get('git_head', 'UNKNOWN')[:12], sources_sha256=meta.get('sources_sha256', 'UNKNOWN')[:16],
               dirty=meta.get('dirty', ''), finished=meta.get('finished', ''),
               regression_pass=reg_pass, regression_total=reg_total,
               suite_exit=' '.join(f'{k}={v}' for k, v in meta.get('status', {}).items()),
               limit_cases=sum(1 for r in tables['limits'] if r['suite'] == 'limits'),
               ext_cases=sum(1 for r in tables['limits'] if r['suite'] == 'ext'),
               fuzz_pass=sum(1 for r in tables['fuzz'] if r['status'] == 'PASS'), fuzz_fail=fuzz_fail,
               fuzz_rejected=meta.get('fuzz_rejected', ''),
               deploy_fit=sum(1 for r in tables['deploy'] if r['fits']), deploy_total=count('deploy'),
               models_measured=count('models'))
    tables['runs'] = [run]
    for name, rows in tables.items():
        if not rows:
            continue
        cols = list(dict.fromkeys(k for r in rows for k in r))
        with (out / f'{name}.csv').open('w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    print(json.dumps(run, indent=1))


def cmd_merge(args):
    """Concatenate the same table across several exported runs (baseline first)."""
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for name in ('runs', 'models', 'layers', 'limits', 'fuzz', 'deploy'):
        rows = []
        for d in args.exports:
            p = Path(d) / f'{name}.csv'
            if p.exists():
                rows += list(csv.DictReader(p.open()))
        if rows:
            cols = list(dict.fromkeys(k for r in rows for k in r))
            with (out / f'{name}.csv').open('w', newline='') as fh:
                w = csv.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                w.writerows(rows)
            print(name, len(rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--label', required=True)
    r.add_argument('--role', choices=['baseline', 'candidate'], default='candidate')
    r.add_argument('--description', default='')
    r.add_argument('--jobs', type=int, default=2)
    r.add_argument('--suites', nargs='+', default=['regression', 'limits', 'ext', 'fuzz', 'models', 'deploy'])
    e = sub.add_parser('export')
    e.add_argument('--label', required=True)
    e.add_argument('--run-dir')
    e.add_argument('--output')
    m = sub.add_parser('merge')
    m.add_argument('--output', required=True)
    m.add_argument('exports', nargs='+')
    a = ap.parse_args()
    sys.exit({'run': cmd_run, 'export': cmd_export, 'merge': cmd_merge}[a.cmd](a) or 0)


if __name__ == '__main__':
    main()
