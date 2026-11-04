#!/usr/bin/env python3
"""Estimate per-model cycles for other MAC array shapes from the measured
4x8 and 4x4 operator traces (docs/validation/int4_v2/zoo_rtl_final.json).

Per Conv/FC operator the trace gives MAC-issue and wait-for-activation cycles;
everything else (result-slot waits, element ops, descriptor/parameter fetch)
is kept as measured on 4x8. Dense layers issue P*ceil(K/R)*ceil(OC/C) vectors:
the column factor comes from the measured 4x4/4x8 ratio, the row factor is
4/R. Depthwise layers issue P*taps*OC/lanes with lanes = R (block-diagonal
mapping on the shared array) or C (a per-column depthwise path).
Activation waits scale with MAC issue (optimistic) or stay at the 4x8 count
per gathered vector (pessimistic: activation bandwidth does not improve).

  python3 docs/tools/array_size_model.py
writes docs/validation/int4_v2/array_model.json
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'docs/validation/int4_v2'
MODELS = ['mnist_l', 'fashion_cnn', 'kws_l', 'kws_m', 'har', 'ecg', 'gtsrb_l']
CONFIGS = {'4x4': (4, 4, False), '4x8': (4, 8, False), '4x16': (4, 16, False), '8x8': (8, 8, False),
           '4x8+dw_path': (4, 8, True)}


def run(rows, model, cols):
    return next(r for r in rows if r['model'] == model and r['cols'] == cols and r['fusion'] == 'all'
                and r['frame'] == 0 and r.get('cycles'))


def estimate(r8, r4, rows_, cols, dw_path, optimistic):
    total = r8['cycles'][0]
    dw_cycles = dense_cycles = 0
    for group, t8, t4 in zip(r8['groups'], r8['operator_trace'][0], r4['operator_trace'][0]):
        if group[0] not in ('Conv', 'FC'):
            continue
        mac8, act8 = t8[7], t8[4]
        if t8[2] >> 13 & 1:  # depthwise
            lanes = cols if dw_path else rows_
            mac = mac8 * 4 / lanes
            act = act8 * mac / mac8 if optimistic else act8 * lanes / 4 * mac / mac8
            dw_cycles += mac8 + act8
        else:
            col = {4: t4[7] / mac8, 8: 1.0, 16: 0.5}[cols]
            row = 4 / rows_
            mac = mac8 * col * row
            act = act8 * col * (row if optimistic else 1)
            dense_cycles += mac8 + act8
        total += mac + act - mac8 - act8
    return total, dw_cycles, dense_cycles


def main():
    rows = json.loads((DATA / 'zoo_rtl_final.json').read_text())
    out = dict(note=__doc__.split('\n\n')[1].replace('\n', ' '), models={})
    for m in MODELS:
        r8, r4 = run(rows, m, 8), run(rows, m, 4)
        base = r8['cycles'][0]
        entry = dict(cycles_4x8=base, cycles_4x4_measured=r4['cycles'][0], configs={})
        for name, (R, C, dw) in CONFIGS.items():
            opt, dw_c, dense_c = estimate(r8, r4, R, C, dw, True)
            pes, _, _ = estimate(r8, r4, R, C, dw, False)
            entry['configs'][name] = dict(optimistic=round(opt), pessimistic=round(pes),
                                          change_optimistic=round(opt / base - 1, 4), change_pessimistic=round(pes / base - 1, 4))
        entry['depthwise_share_4x8'] = round(dw_c / base, 4)
        entry['dense_share_4x8'] = round(dense_c / base, 4)
        out['models'][m] = entry
        print(m, f"DW {entry['depthwise_share_4x8']:.0%}",
              {k: f"{v['change_optimistic']:+.0%}..{v['change_pessimistic']:+.0%}" for k, v in entry['configs'].items()})
    (DATA / 'array_model.json').write_text(json.dumps(out, indent=1, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
