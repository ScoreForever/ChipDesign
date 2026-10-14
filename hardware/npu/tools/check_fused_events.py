#!/usr/bin/env python3
"""Independent 4x8 fused-core event scoreboard; RTL is the timing reference.

Checks every sampled edge, not just final profiler totals. No DUT scheduler,
inflight counters or arithmetic helper functions are called by this model.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from tinycnn8_golden import requant


def signed(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def lanes(value, bits, count):
    return [signed(value >> (bits * i), bits) for i in range(count)]


def read_hex(path):
    return [int(x, 16) for x in Path(path).read_text().split()]


def check_trace(path, inputs, weights, bias, multiplier, shift, classes=4):
    """inputs is one 320-byte sequence per job; a single sequence may be reused."""
    jobs = []
    state = None
    with Path(path).open() as stream:
        for row in csv.DictReader(stream):
            def n(key):
                return int(row[key])

            def h(key):
                return int(row[key], 16)

            def require(ok, message):
                if not ok:
                    raise AssertionError(f"job={row['job']} cycle={row['cycle']}: {message}")

            if n('start'):
                require(state is None, 'start before previous job drained')
                require(n('job') == len(jobs) and n('cycle') == 0, 'job/start order')
                image = inputs[0 if len(inputs) == 1 else n('job')]
                require(len(image) == 320, 'input size')
                memory = [signed(x, 8) for x in image] + [None] * 704
                state = dict(cycle=0, phase=0, clocks=[0]*3, loads=[0]*3,
                             issues=[0]*3, retires=[0]*3, reads=[0]*3,
                             writes=[0]*3, pending={}, quant={}, live=[0, 0],
                             banks=[[None]*4 for _ in range(2)], masks=[0, 0],
                             groups=[-1, -1], accum={}, windows={}, ready={},
                             logits=[0]*8, peak=0, memory=memory)
                require(not n('busy') and not any(n(k) for k in
                        ('load', 'issue', 'retire', 'rq', 'qout', 'write', 'done')), 'start edge events')
            else:
                require(state is not None, 'event outside job')
                state['cycle'] += 1
                require(n('cycle') == state['cycle'], 'missing/duplicate clock edge')
                require(n('busy') == 1, 'busy dropped before completion')
                phase = n('phase')
                if phase != state['phase']:
                    require(phase == state['phase'] + 1, 'phase order')
                    require(not state['pending'] and not state['quant'], 'phase changed with work pending')
                    state['banks'] = [[None]*4 for _ in range(2)]
                    state['masks'] = [0, 0]
                    state['groups'] = [-1, -1]
                    state['accum'] = {}
                    state['windows'] = {}
                    state['ready'] = {}
                    state['phase'] = phase
                state['clocks'][phase] += 1
                state['peak'] = max(state['peak'], sum(state['live']))
                cycle = state['cycle']
                groups, positions = ((3, 16), (18, 16), (40, 1))[phase]

                # A live bank must not be overwritten, even on its last retire edge.
                if n('load'):
                    ordinal = state['loads'][phase]
                    sequence, expected_row = divmod(ordinal, 4)
                    bank = sequence % 2
                    require((n('lb'), n('lr')) == (bank, expected_row), 'weight row/bank order')
                    require(state['live'][bank] == 0, 'weight bank overwritten while live')
                    group = sequence % groups
                    addr = (0, 9, 81)[phase] + group * (3 if phase == 0 else 4) + expected_row
                    expected = 0 if phase == 0 and expected_row == 3 else weights[addr]
                    require(n('la') == addr and h('lw') == expected, 'weight address/data')
                    if expected_row == 0:
                        state['masks'][bank] = 0
                        state['groups'][bank] = sequence
                    state['banks'][bank][expected_row] = lanes(expected, 8, 8)
                    state['masks'][bank] |= 1 << expected_row
                    state['loads'][phase] += 1

                due = state['pending'].pop(cycle, None)
                require(bool(n('retire')) == (due is not None), 'array retirement latency (11 edges)')
                if due:
                    bank, tag, products, group, tile, position = due
                    require((n('rb'), h('rtag')) == (bank, tag), 'retirement bank/tag')
                    require(lanes(h('psum'), 32, 8) == products, 'array dot product')
                    base = [signed(x, 32) for x in bias[(0, 8, 24)[phase]:(8, 16, 32)[phase]]]
                    previous = base if group == 0 else state['accum'][position]
                    accum = [signed(a+b, 32) for a, b in zip(previous, products)]
                    require(lanes(h('rsum'), 32, 8) == accum, 'partial sum/bias accumulation')
                    state['accum'][position] = accum
                    if group == groups - 1:
                        if phase == 2:
                            state['logits'] = accum[:classes] + [0]*(8-classes)
                        else:
                            key = (tile, position // 4)
                            if position % 4 == 0:
                                state['windows'][key] = accum
                            else:
                                state['windows'][key] = [max(a, b) for a, b in
                                                       zip(state['windows'][key], accum)]
                            if position % 4 == 3:
                                state['ready'][key] = cycle + 1
                    state['live'][bank] -= 1
                    require(state['live'][bank] >= 0, 'bank occupancy underflow')
                    state['retires'][phase] += 1

                if n('issue'):
                    ordinal = state['issues'][phase]
                    sequence, position = divmod(ordinal, positions)
                    tile, group = divmod(sequence, groups)
                    bank = sequence % 2
                    tag = (tile << 11) | ((group == 0) << 6) | ((group == groups-1) << 5) | position
                    require((n('ib'), h('itag')) == (bank, tag), 'issue order/bank/tag')
                    require(state['masks'][bank] == 15 and state['groups'][bank] == sequence,
                            'issue before its complete weight group')
                    memory = state['memory']
                    if phase == 2:
                        addr = 4 * group
                        act = memory[addr:addr+4]
                    else:
                        width, height = ((16, 20), (8, 10))[phase]
                        win_width = width // 2
                        wy, wx = divmod(tile*4 + position//4, win_width)
                        oy, ox = 2*wy + (position//2)%2, 2*wx + position%2
                        if phase == 0:
                            iy = oy + group - 1
                            addr = iy*16 + ox-1
                            act = [memory[iy*16+ox-1+r] if 0 <= iy < height and
                                   0 <= ox-1+r < width else 0 for r in range(3)] + [0]
                        else:
                            ky, kx = divmod(group//2, 3)
                            iy, ix = oy+ky-1, ox+kx-1
                            addr = 320+(iy*8+ix)*8+(group%2)*4
                            act = memory[addr:addr+4] if 0 <= iy < height and 0 <= ix < width else [0]*4
                    require(n('ibase') == addr % 1024, 'activation address')
                    require(act == lanes(h('ia'), 8, 4), 'activation gather/padding/data')
                    products = [signed(sum(act[r]*state['banks'][bank][r][c] for r in range(4)), 32)
                                for c in range(8)]
                    state['pending'][cycle+11] = (bank, tag, products, group, tile, position)
                    state['live'][bank] += 1
                    state['issues'][phase] += 1

                if n('rq'):
                    require(phase < 2, 'FC must bypass requant')
                    ordinal = state['reads'][phase]
                    window, lane = divmod(ordinal, 8)
                    tile, win = divmod(window, 4)
                    key = (tile, win)
                    require(key in state['ready'] and state['ready'][key] <= cycle, 'pool read before ready')
                    addr = (320 if phase == 0 else 0) + ordinal
                    value = state['windows'][key][lane]
                    mult = signed(multiplier[phase*8+lane], 32)
                    sh = signed(shift[phase*8+lane], 6)
                    require(mult >= 0 and -31 <= sh <= 31, 'unsupported fused quantization parameter')
                    require((n('qa'), signed(h('qv'), 32), signed(h('qm'), 32), signed(h('qs'), 6)) ==
                            (addr, value, mult, sh), 'pool/quantizer input order or value')
                    state['quant'][cycle+4] = (addr, requant(value, 0, mult, sh, 0, 0, 127))
                    state['reads'][phase] += 1
                due_q = state['quant'].pop(cycle, None)
                require(bool(n('qout')) == (due_q is not None), 'requant retirement latency (4 edges)')
                require(n('write') == n('qout'), 'write without quantizer retirement')
                if due_q:
                    require((n('oa'), h('ov'), n('wa'), h('wv')) == (*due_q, *due_q), 'requant/write data or address')
                    state['memory'][due_q[0]] = due_q[1]
                    state['writes'][phase] += 1

            require(n('total') == state['cycle'], 'profiler total differs from independent edges')
            require([n('c1'), n('c2'), n('fc')] == state['clocks'], 'profiler phase counters')
            require(n('weights') == sum(state['loads']), 'weight profiler')
            require(n('issues') == sum(state['issues']) and n('retires') == sum(state['retires']), 'transaction profiler')
            require(n('peak') == state['peak'], 'peak inflight profiler')
            require(lanes(h('logits'), 32, 8) == state['logits'], 'logit write timing/data')
            require(n('valid') == n('done'), 'profile valid/completion timing')
            if n('done'):
                require(not state['pending'] and not state['quant'] and state['live'] == [0, 0], 'completion before drain')
                require(state['clocks'] == [1000, 1480, 325], '4x8 calibrated phase timing')
                require(state['loads'] == [240, 360, 160], 'incomplete weight coverage')
                require(state['issues'] == state['retires'] == [960, 1440, 40], 'incomplete array coverage')
                require(state['reads'] == state['writes'] == [640, 160, 0], 'incomplete pooled write coverage')
                jobs.append(dict(job=len(jobs), cycles=state['cycle'], phase_cycles=state['clocks'],
                                 issues=sum(state['issues']), weight_rows=sum(state['loads']),
                                 quantized_bytes=sum(state['writes']), peak_inflight=state['peak']))
                state = None
    if state is not None or not jobs:
        raise AssertionError('truncated or empty event trace')
    return jobs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trace', required=True, type=Path)
    p.add_argument('--data-dir', required=True, type=Path)
    p.add_argument('--classes', default=4, type=int)
    args = p.parse_args()
    d = args.data_dir
    result = check_trace(args.trace, [read_hex(d/'input.hex')], read_hex(d/'weights.hex'),
                         read_hex(d/'bias.hex'), read_hex(d/'multiplier.hex'), read_hex(d/'shift.hex'), args.classes)
    print(json.dumps({'status': 'PASS', 'jobs': result}, indent=2))


if __name__ == '__main__':
    main()
