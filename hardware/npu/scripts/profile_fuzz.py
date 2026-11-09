#!/usr/bin/env python3
"""Randomized deep-limit tests across the ABI 5.0 model envelope.

Generates seeded random models inside the compiler's documented envelope
(kernels 1..7 incl. rectangular, stride 1/2, asymmetric padding, odd IC/OC,
depthwise, 1D, fused/standalone pooling, residual Add, u4/i32 inputs, FC up to
K=1024, fusion on/off) and runs each through the real compiler, host API and
RTL (run_program_regression.simulate checks every tensor write, cold + warm).

Records cycles plus per-resource analytic bounds, so performance cliffs show
up as low bound efficiency. Compiler rejections are recorded with the reason.
No production RTL is edited.
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


class Builder:
    def __init__(self, r, shape, dtype):
        self.r, self.nodes, self.n = r, [], 0
        self.cur, self.shape, self.dtype = 'x', list(shape), dtype
        self.history = [('x', list(shape), dtype)]
        self.tags = []

    def name(self):
        self.n += 1
        return f't{self.n}'

    def emit(self, node, shape, dtype):
        node['output'] = self.name()
        self.nodes.append(node)
        self.cur, self.shape, self.dtype = node['output'], shape, dtype
        if dtype in ('i4', 'u4'):
            self.history.append((self.cur, list(shape), dtype))

    def requant(self, k=64):
        r, c = self.r, self.shape[2]
        unsigned = r.random() < 0.4
        shift = -max(1, min(20, int(math.log2(max(k, 1))) + r.randint(1, 4)))
        node = dict(op='Requant', input=self.cur, multiplier=[r.randrange(1 << 13, 1 << 15) for _ in range(c)],
                    shift=[shift] * c)
        if unsigned:
            node['dtype'] = 'u4'
        self.emit(node, self.shape, 'u4' if unsigned else 'i4')
        if r.random() < 0.3:
            self.emit(dict(op='ReLU', input=self.cur), self.shape, self.dtype)

    def conv(self, depthwise=False, pointwise=False):
        r = self.r
        h, w, c = self.shape
        if pointwise:
            kh = kw = 1
        else:
            kh = 1 if h == 1 else r.randint(1, min(7, h + 2))
            kw = r.randint(1, min(7, w + 2))
        sh = 1 if h == 1 else r.choice((1, 1, 2))
        sw = r.choice((1, 1, 2))
        pads = [r.randrange(kh), r.randrange(kw), r.randrange(kh), r.randrange(kw)]
        if h == 1:
            pads[0] = pads[2] = 0
        if h + pads[0] + pads[2] < kh or w + pads[1] + pads[3] < kw:
            pads = [kh - 1 if h < kh else 0, kw - 1 if w < kw else 0, 0, 0]
        oc = c if depthwise else r.choice((r.randint(1, 16), r.randint(8, 48), 8 * r.randint(1, 6)))
        k = kh * kw * (1 if depthwise else c)
        if k > 1024:
            return False
        oh, ow = (h + pads[0] + pads[2] - kh) // sh + 1, (w + pads[1] + pads[3] - kw) // sw + 1
        node = dict(op='Conv', input=self.cur, channels=oc, kernel=[kh, kw], stride=[sh, sw], pads=pads,
                    weights=[r.randrange(-8, 8) for _ in range(k * oc)],
                    bias=[r.randrange(-200, 201) for _ in range(oc)])
        if depthwise:
            node['groups'] = c
        self.tags.append('dw' if depthwise else 'pw' if pointwise else f'k{kh}x{kw}s{sh}{sw}')
        self.emit(node, [oh, ow, oc], 'i32')
        self.requant(k)
        return True

    def pool(self):
        r = self.r
        h, w, c = self.shape
        avg = r.random() < 0.35
        if r.random() < 0.5 or h < 2 or w < 2:
            kh, kw = (1 if h == 1 else r.randint(1, min(4, h))), r.randint(1, min(4, w))
            sh, sw = r.randint(1, max(1, kh)), r.randint(1, max(1, kw))
            node = dict(op='AvgPool' if avg else 'MaxPool', input=self.cur, kernel=[kh, kw], stride=[sh, sw])
        else:
            kh, kw, sh, sw = 2, 2, 2, 2
            node = dict(op='AvgPool' if avg else 'MaxPool', input=self.cur)
        oh, ow = (h - kh) // sh + 1, (w - kw) // sw + 1
        self.tags.append('avgpool' if avg else 'maxpool')
        self.emit(node, [oh, ow, c], 'i32' if avg else self.dtype)
        if avg:
            self.requant(kh * kw * 8)

    def add(self):
        r = self.r
        same = [t for t in self.history[:-1] if t[1] == self.shape]
        if not same:
            return False
        other = r.choice(same)[0]
        self.tags.append('add')
        self.emit(dict(op='Add', input=self.cur, input2=other, weights=[r.randint(-128, 127), r.randint(-128, 127)]),
                  self.shape, 'i32')
        self.requant(256)
        return True

    def fc(self):
        r = self.r
        k = math.prod(self.shape)
        if k > 1024:
            return False
        oc = r.choice((r.randint(1, 12), r.randint(1, 64)))
        self.tags.append('fc')
        self.emit(dict(op='FC', input=self.cur, channels=oc, weights=[r.randrange(-8, 8) for _ in range(k * oc)],
                       bias=[r.randrange(-200, 201) for _ in range(oc)]), [1, 1, oc], 'i32')
        if r.random() < 0.5:
            self.requant(k)
        return True


def generate(seed):
    r = random.Random(seed)
    family = r.choice(('chain', 'chain', 'dsblock', 'oned', 'residual', 'mlp', 'elementwise'))
    if family == 'oned':
        shape = [1, r.randint(8, 128), r.randint(1, 16)]
    elif family == 'mlp':
        shape = [1, 1, r.randint(1, 1024)]
    else:
        shape = [r.randint(1, 20), r.randint(1, 20), r.choice((1, 2, 3, 4, 5, 7, 8, 12, 16, 24))]
    dtype = r.choice(('i4', 'i4', 'u4')) if family != 'elementwise' else r.choice(('i4', 'u4', 'i32'))
    b = Builder(r, shape, dtype)
    if dtype == 'i32':
        b.tags.append('i32in')
        b.requant(16)
    steps = r.randint(1, 4)
    for _ in range(steps):
        if family in ('chain', 'oned'):
            b.conv() if r.random() < 0.7 else b.pool()
        elif family == 'dsblock':
            c = b.shape[2]
            if c % 8:
                b.conv(pointwise=True)
                # force a multiple of 8 for the depthwise stage
                if b.shape[2] % 8:
                    continue
            b.conv(depthwise=True)
            b.conv(pointwise=True)
        elif family == 'residual':
            b.conv()
            b.add()
        elif family == 'mlp':
            b.fc() if b.dtype in ('i4', 'u4') else b.requant(64)
        else:
            choice = r.choice(('pool', 'relu', 'add'))
            if choice == 'pool':
                b.pool()
            elif choice == 'relu':
                b.tags.append('relu')
                b.emit(dict(op='ReLU', input=b.cur), b.shape, b.dtype)
            elif not b.add():
                b.pool()
    if family != 'mlp' and r.random() < 0.4 and b.dtype in ('i4', 'u4'):
        b.fc()
    if not b.nodes:
        b.tags.append('relu')
        b.emit(dict(op='ReLU', input='x'), b.shape, b.dtype)
    model = dict(input=dict(name='x', shape=shape, dtype=dtype), nodes=b.nodes)
    if dtype == 'i32':
        frame = [r.randrange(-3000, 3000) for _ in range(math.prod(shape))]
    elif dtype == 'u4':
        frame = [r.randrange(0, 16) for _ in range(math.prod(shape))]
    else:
        frame = [r.randrange(-8, 8) for _ in range(math.prod(shape))]
    fused = r.random() < 0.8
    return family, b.tags, model, frame, fused


def bounds(row):
    """Resource bound from measured counters: MAC issues vs. shared-port traffic."""
    p = row['metrics']
    port = p['model_requests'] + p['activation_requests']
    return dict(port_requests=port, bound_max=max(p['mac_issues'], port, p['post_lanes'] // 2),
                bound_efficiency_pct=100 * max(p['mac_issues'], port, p['post_lanes'] // 2) / p['active_cycles'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--count', type=int, default=60)
    ap.add_argument('--seed', type=int, default=20261109)
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--memory', choices=sorted(sim_build.MEMORY), default='fpga_mem',
                    help='memory configuration (see sim_build.py)')
    ap.add_argument('--max-pixels', type=int, default=6000, help='skip cases whose largest tensor exceeds this many elements')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources = sim_build.watched_sources(args.memory)
    before = {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in sources}
    cases, rejected, seed = [], [], args.seed
    while len(cases) < args.count:
        family, tags, model, frame, fused = generate(seed)
        try:
            pkg = regression.compile_model(model, cols=8, fuse=fused)
            logical = regression.reference(model, frame)
            if max(len(v) for v in logical.values()) > args.max_pixels:
                raise ValueError('skipped: simulation size cap')
            cases.append((f'fz{seed}', family, tags, model, frame, fused, pkg.manifest['op_count']))
        except (ValueError, AssertionError) as e:
            rejected.append(dict(seed=seed, family=family, reason=str(e)[:120]))
        seed += 1
    image = out / 'program.vvp'
    sim_build.compile_program_tb(image, out / 'compile.log', memory=args.memory, tile=16)

    def measure(case):
        name, family, tags, model, frame, fused, ops = case
        try:
            row = regression.simulate(out, image, name, model, frame, 8, fused, False, True)
        except Exception as e:  # keep going; a functional failure is a finding
            print(f'FAIL {name} {family} {tags}: {str(e)[:200]}', flush=True)
            return dict(case=name, family=family, tags=tags, fused=fused, failed=str(e)[:2000], model=model)
        row['metrics'] = pb.annotate(row, model, frame)
        row['metrics'].update(bounds(row))
        row.update(family=family, tags=tags, fused=fused, ops=ops)
        print(f'PASS {name} {family} ops={ops} cycles={row["cycles"][0]} eff={row["metrics"]["bound_efficiency_pct"]:.0f}%', flush=True)
        return row

    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(measure, cases))
    assert before == {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in sources}, 'RTL changed'
    failed = [r for r in results if 'failed' in r]
    (out / 'summary.json').write_text(json.dumps(dict(sources_sha256=before, seed=args.seed, rejected=rejected,
        tests=results), indent=1) + '\n')
    ok = [r for r in results if 'failed' not in r]
    with (out / 'metrics.csv').open('w', newline='') as f:
        fields = ['case', 'family', 'tags', 'fused', 'ops', *ok[0]['metrics']]
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(dict(case=r['case'], family=r['family'], tags='+'.join(r['tags']), fused=r['fused'],
                          ops=r['ops'], **r['metrics']) for r in ok)
    print(f'{len(ok)} PASSED, {len(failed)} FAILED, {len(rejected)} REJECTED BY COMPILER', flush=True)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
