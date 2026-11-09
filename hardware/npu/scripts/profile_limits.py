#!/usr/bin/env python3
"""Extended limit tests for the ABI 5.0 8x8 NPU (complements profile_bottlenecks --limits-only).

Each workload saturates one axis the base limit suite does not cover:
gather irregularity (IC not a multiple of 8), kernel size/stride, depthwise width,
standalone elementwise throughput, fused pooling and large spatial extents.
Every run goes through the real compiler/host API and checks every tensor write
(run_program_regression.simulate). No production RTL is edited.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'hardware/npu/scripts'))
import profile_bottlenecks as pb
import run_program_regression as regression
import sim_build

R = random.Random(11)


def frame(shape, lo=-8, hi=8):
    return [R.randrange(lo, hi) for _ in range(math.prod(shape))]


def conv(src, dst, ic, oc, kernel, depthwise=False, **kw):
    kh, kwid = (kernel, kernel) if isinstance(kernel, int) else kernel
    k = kh * kwid * (1 if depthwise else ic)
    node = dict(op='Conv', input=src, output=dst, channels=oc, kernel=kernel,
                weights=[R.randrange(-8, 8) for _ in range(k * oc)],
                bias=[R.randrange(-50, 51) for _ in range(oc)], **kw)
    if depthwise:
        node['groups'] = oc
    return node


def requant(src, dst, c, shift=-6):
    return dict(op='Requant', input=src, output=dst, multiplier=[0x4000] * c, shift=[shift] * c)


def workloads():
    # A. Gather irregularity: 3x3/pad1 conv on 8x8, OC=8; only IC changes.
    for ic in (1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 16, 24):
        shape = [8, 8, ic]
        yield f'gather_ic{ic}', dict(input=dict(name='x', shape=shape), nodes=[
            conv('x', 'a', ic, 8, 3, pads=[1] * 4), requant('a', 'y', 8)]), frame(shape), True
    # B. Kernel size and stride at IC=8, OC=8, 12x12 input, same padding.
    for k in (1, 3, 5, 7):
        for s in (1, 2):
            p = k // 2
            shape = [12, 12, 8]
            yield f'kernel{k}_s{s}', dict(input=dict(name='x', shape=shape), nodes=[
                conv('x', 'a', 8, 8, k, stride=s, pads=[p] * 4), requant('a', 'y', 8)]), frame(shape), True
    # C. Depthwise width: 3x3/pad1 on 8x8.
    for c in (8, 16, 32, 64):
        shape = [8, 8, c]
        yield f'dw_c{c}', dict(input=dict(name='x', shape=shape), nodes=[
            conv('x', 'a', c, c, 3, depthwise=True, pads=[1] * 4), requant('a', 'y', c)]), frame(shape), True
    # D. Standalone elementwise throughput on 16x16xC.
    for c in (8, 32):
        shape = [16, 16, c]
        yield f'relu_c{c}', dict(input=dict(name='x', shape=shape), nodes=[
            dict(op='ReLU', input='x', output='y')]), frame(shape), True
        yield f'maxpool2_c{c}', dict(input=dict(name='x', shape=shape), nodes=[
            dict(op='MaxPool', input='x', output='y')]), frame(shape), True
        yield f'avgpool3_c{c}', dict(input=dict(name='x', shape=shape), nodes=[
            dict(op='AvgPool', input='x', output='a', kernel=3, stride=1),
            requant('a', 'y', c, -3)]), frame(shape), True
        yield f'add_c{c}', dict(input=dict(name='x', shape=shape), nodes=[
            dict(op='ReLU', input='x', output='a'),
            dict(op='Add', input='x', input2='a', output='b', weights=[3, -5]),
            requant('b', 'y', c, -3)]), frame(shape), True
    # E. Fused vs. unfused Conv->MaxPool (2x2) at 16x16x8 -> 8 channels.
    shape = [16, 16, 8]
    model = dict(input=dict(name='x', shape=shape), nodes=[
        conv('x', 'a', 8, 8, 3, pads=[1] * 4), requant('a', 'b', 8),
        dict(op='ReLU', input='b', output='c'), dict(op='MaxPool', input='c', output='y')])
    data = frame(shape)
    yield 'conv_pool', model, data, True
    yield 'conv_pool', model, data, False
    # F. Large spatial extent: 32x32x8 3x3 conv, OC 8 and 16.
    for oc in (8, 16):
        shape = [32, 32, 8]
        yield f'spatial32_oc{oc}', dict(input=dict(name='x', shape=shape), nodes=[
            conv('x', 'a', 8, oc, 3, pads=[1] * 4), requant('a', 'y', oc)]), frame(shape), True


def bounds(model, fused):
    """Analytic lower bounds (cycles) per resource, from the logical graph.

    Post-processing drains two nibble lanes per cycle; a Requant/ReLU fused into
    its producer adds no extra pass. Elementwise reads move eight channels each.
    """
    shapes = {model['input']['name']: model['input']['shape']}
    mac_issue = post = elem_reads = 0
    for n in model['nodes']:
        h, w, c = shapes[n['input']]
        op = n['op']
        if op in ('Conv', 'FC'):
            kh, kwid = ((n.get('kernel', 1),) * 2 if isinstance(n.get('kernel', 1), int) else n['kernel'])
            st = n.get('stride', 1)
            sh, sw = (st, st) if isinstance(st, int) else st
            pt, pl, pb_, pr = n.get('pads', [0] * 4)
            if op == 'FC':
                kh = kwid = 1; sh = sw = 1; pt = pl = pb_ = pr = 0; k = h * w * c; h = w = 1
            oh = (h + pt + pb_ - kh) // sh + 1
            ow = (w + pl + pr - kwid) // sw + 1
            oc = n['channels']
            if op == 'FC':
                groups = math.ceil(k / 8)
            elif n.get('groups'):
                groups = kh * kwid  # one tap per K group for depthwise (COLS=8)
            elif c == 1 and kwid <= 8 and kh > 1:
                groups = kh
            else:
                groups = math.ceil(kh * kwid * c / 8)
            mac_issue += oh * ow * groups * math.ceil(oc / 8)
            shapes[n['output']] = [oh, ow, oc]
            post += oh * ow * math.ceil(oc / 2)
        elif op in ('MaxPool', 'AvgPool'):
            k = n.get('kernel', 2); kh, kwid = (k, k) if isinstance(k, int) else k
            s = n.get('stride', k if 'kernel' in n else 2); sh, sw = (s, s) if isinstance(s, int) else s
            oh, ow = (h - kh) // sh + 1, (w - kwid) // sw + 1
            shapes[n['output']] = [oh, ow, c]
            elem_reads += oh * ow * kh * kwid * math.ceil(c / 8)
            post += oh * ow * math.ceil(c / 2)
        elif op == 'Add':
            shapes[n['output']] = [h, w, c]
            elem_reads += 2 * h * w * math.ceil(c / 8)
            post += h * w * math.ceil(c / 2)
        else:
            shapes[n['output']] = [h, w, c]
            standalone = not fused or n['input'] == model['input']['name']
            if standalone:
                elem_reads += h * w * math.ceil(c / 8)
                post += h * w * math.ceil(c / 2)
    return dict(bound_mac_issue=mac_issue, bound_elem_reads=elem_reads, bound_post_fires=post,
                bound_max=max(mac_issue, elem_reads, post))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--tile', type=int, choices=[8, 16], default=16)
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--memory', choices=sorted(sim_build.MEMORY), default='fpga_mem',
                    help='memory configuration (see sim_build.py)')
    ap.add_argument('--only', nargs='*', help='case-name prefixes')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources = sim_build.watched_sources(args.memory)
    before = {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in sources}
    cases = [c for c in workloads() if not args.only or any(c[0].startswith(p) for p in args.only)]
    for name, model, data, fused in cases:
        regression.compile_model(model, cols=8, fuse=fused)
    folder = out / f'tile{args.tile}'
    folder.mkdir()
    image = folder / 'program.vvp'
    sim_build.compile_program_tb(image, folder / 'compile.log', memory=args.memory, tile=args.tile)

    def measure(case):
        name, model, data, fused = case
        row = regression.simulate(folder, image, name, model, data, 8, fused, False, True)
        m = pb.annotate(row, model, data)
        m.update(bounds(model, fused))
        m['bound_efficiency_pct'] = 100 * m['bound_max'] / m['active_cycles']
        row.update(tile=args.tile, fused=fused, metrics=m)
        print(f'PASS {row["case"]}: {row["cycles"]} bound={m["bound_max"]}', flush=True)
        return row

    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(measure, cases))
    assert before == {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in sources}, 'RTL changed'
    (out / 'summary.json').write_text(json.dumps(dict(sources_sha256=before, tests=results,
        scope=f'RTL limit experiment, memory={args.memory}, two repeats, every tensor write checked'), indent=2) + '\n')
    with (out / 'metrics.csv').open('w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=['case', 'fused', *results[0]['metrics']])
        wr.writeheader()
        wr.writerows(dict(case=r['case'], fused=r['fused'], **r['metrics']) for r in results)
    print(f'ALL {len(results)} EXTENDED LIMIT CASES PASSED', flush=True)


if __name__ == '__main__':
    main()
