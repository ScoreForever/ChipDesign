#!/usr/bin/env python3
"""Check fragmented INT4 gather correctness and its IC4/3x3 performance budget."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import run_program_regression as r

ROOT = Path(__file__).resolve().parents[3]


def cases():
    rng = random.Random(20261108)
    for ic in (2, 3, 4, 5, 7, 9):
        for unsigned in (False, True):
            model = dict(input=dict(name='x', shape=[5, 7, ic], dtype='u4' if unsigned else 'i4'), nodes=[
                dict(op='Conv', input='x', output='a', channels=9, kernel=[3, 2],
                     stride=[2, 1], pads=[2, 1, 0, 1],
                     weights=[rng.randrange(-8, 8) for _ in range(ic * 6 * 9)], bias=[0] * 9),
                dict(op='Requant', input='a', output='y', multiplier=[16384] * 9, shift=[-6] * 9)])
            yield f'fragment_ic{ic}_u{int(unsigned)}', model, [rng.randrange(16) if unsigned else rng.randrange(-8, 8) for _ in range(35 * ic)]
    # Single output window makes every cursor position a row-end position;
    # exercise all precomputed pooling steps, including the wrap-only cases.
    for height in (1, 2):
        model = dict(input=dict(name='x', shape=[height, 2, 4], dtype='i4'), nodes=[
            dict(op='Conv', input='x', output='a', channels=8, kernel=1,
                 weights=[i % 15 - 7 for i in range(32)], bias=[0]*8),
            dict(op='Requant', input='a', output='b', multiplier=[16384]*8, shift=[-3]*8),
            dict(op='MaxPool', input='b', output='y', kernel=[height, 2], stride=[height, 2])])
        yield f'pool{height}d_single_window', model, [i % 16 - 8 for i in range(height * 8)]
    yield 'ic4k3_budget', dict(input=dict(name='x', shape=[8, 8, 4], dtype='i4'), nodes=[
        dict(op='Conv', input='x', output='a', channels=8, kernel=3, pads=[1]*4,
             weights=[i % 15 - 7 for i in range(36*8)], bias=[0]*8),
        dict(op='Requant', input='a', output='y', multiplier=[16384]*8, shift=[-5]*8)]), [i % 16 - 8 for i in range(256)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--jobs', type=int, default=4)
    args = ap.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    sources = (ROOT/'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
    hashes = {s: hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources}
    image = out/'program.vvp'
    r.run(['iverilog', '-g2012', '-s', 'tb_npu_program', '-DSYNTHESIS',
           '-DCHIPDESIGN_FPGA_MEM', '-DNPU_ENABLE_WAVE_TRACE=0', '-o', image,
           *sources, 'hardware/soc/sim/tb/tb_npu_program.sv'], out/'compile.log')
    def check(case):
        name, model, frame = case
        row = r.simulate(out, image, name, model, frame, 8, True, False, True)
        if name == 'ic4k3_budget':
            assert max(row['cycles']) <= 1050, 'fragmented gather cycle budget exceeded'
            assert all(p[6] <= 340 for p in row['performance']), 'fragmented gather read budget exceeded'
        print('PASS', name, row['cycles'], flush=True)
        return row
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(check, cases()))
    assert hashes == {s: hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources}
    (out/'summary.json').write_text(json.dumps(dict(tests=rows, sources_sha256=hashes), indent=2)+'\n')
    print(f'ALL {len(rows)} GATHER CASES PASSED', flush=True)


if __name__ == '__main__':
    main()
