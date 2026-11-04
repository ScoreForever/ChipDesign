#!/usr/bin/env python3
"""Flatten docs/validation/int4_v2 results into the row tables behind the
interactive INT4 v2 report page (docs/validation/int4_v2/dashboard/*.json).

Run after int4_v2_report.py, which writes int4_v2_summary.json (MAC counts
need the exported models on PKUh100)."""
from __future__ import annotations
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from int4_v2_report import DATA, ROOT, CORE_MHZ, TCK_MHZ, RPC_MS, NAMES, ORDER, load, runs_by, perf_of

OUT = DATA / 'dashboard'
DATASETS = {'mnist_l': 'MNIST', 'fashion_cnn': 'Fashion-MNIST', 'kws_l': 'Speech Commands v2', 'kws_m': 'Speech Commands v2',
            'har': 'UCI-HAR', 'ecg': 'MIT-BIH', 'gtsrb_l': 'GTSRB'}
PHASES = [('capabilities', 'Capability query'), ('model_upload', 'Model upload'), ('model_commit', 'Model commit'),
          ('input_upload', 'Input upload'), ('start', 'Start'), ('compute', 'Compute'), ('status_poll', 'Status poll'),
          ('output_readout', 'Output readout'), ('ack', 'Ack')]


def main():
    summary = json.loads((ROOT / 'docs/validation/int4_v2_summary.json').read_text())
    ev = {r['model']: r for r in load('zoo_eval.json', [])}
    final_rows = load('zoo_rtl_final.json')
    final = runs_by(final_rows)
    t = {}

    t['models'] = []
    for m in ORDER:
        s, r = summary['models'][m], ev[m]
        t['models'].append(dict(model=NAMES[m], dataset=DATASETS[m], test_samples=r['test_samples'],
                                float_test=round(s['float_test'], 4), int4_test=round(s['int4_test'], 4),
                                int4_balanced=round(s['int4_test_balanced'], 4), weights=s['weights'],
                                model_bytes=s['model_bytes'], activation_peak=s['activation_peak'],
                                descriptors=s['descriptors'], cycles_c8=s['cycles_c8'], cycles_c4=s['cycles_c4'],
                                latency_us=round(s['cycles_c8'] / CORE_MHZ, 1), macs=s.get('macs'),
                                mac_utilization=round(s['mac_utilization'], 4) if s.get('mac_utilization') else None))

    labels = dict(mac_issue='MAC issue', wait_activation='Wait activation', wait_weight='Wait weight',
                  wait_slot='Wait result slot', kernel_prepare='Kernel prepare', element_and_other='Element / other')
    t['breakdown'] = [dict(model=NAMES[m], category=labels[k], cycles=v)
                      for m in ORDER for k, v in summary['models'][m]['breakdown'].items()]

    stages = [('ABI4 first cut', 'zoo_rtl_stage1.json'), ('+ vector element, row pad', 'zoo_rtl_stage2.json'),
              ('+ dual requant (freeze)', 'zoo_rtl_final.json')]
    t['stages'] = [dict(model=NAMES[m], stage=label, cycles=runs_by(load(f, []))[m]['cycles'][0])
                   for label, f in stages for m in ORDER if m in runs_by(load(f, []))]
    tile8 = runs_by(load('zoo_rtl_tile8.json', []))
    t['tile8'] = [dict(model=NAMES[m], tile16=final[m]['cycles'][0], tile8=tile8[m]['cycles'][0]) for m in ORDER if m in tile8]

    t['fusion'] = []
    for m in ORDER:
        for fusion, label in (('all', 'All fused'), ('no_requant', 'No requant fusion'), ('no_relu', 'No ReLU fusion'),
                              ('no_maxpool', 'No maxpool fusion'), ('none', 'Nothing fused')):
            r = next((x for x in final_rows if x['model'] == m and x['cols'] == 8 and x['fusion'] == fusion and x['frame'] == 0), None)
            if r is None:
                continue
            fits = 'cycles' in r
            t['fusion'].append(dict(model=NAMES[m], config=label, fits=fits,
                                    cycles=r['cycles'][0] if fits else None, writes=r['writes'][0] if fits else None,
                                    limit=None if fits else ('16 descriptors' if 'operators' in r['unsupported'] else
                                                             'activation SRAM' if 'activation' in r['unsupported'] else 'model SRAM')))

    t['e2e'], t['host'] = [], []
    for r in load('e2e.json', []):
        m = r['model']
        cyc = final.get(m, {}).get('cycles', [r['compute_cycles']])[0]
        proto = 'Burst' if r['protocol'] == 'burst' else 'Per-word (ABI 3)'
        for key, label in PHASES:
            ms = cyc / CORE_MHZ / 1000 if key == 'compute' else r['phases'].get(key, {}).get('tck', 0) / TCK_MHZ / 1000
            t['e2e'].append(dict(model=NAMES[m], protocol=proto, phase=label, ms=round(ms, 3)))
        rpc = r['commands'] * 3 + (math.ceil(r['burst_scans'] / 64) + 4 if r['protocol'] == 'burst' else 0)
        t['host'].append(dict(model=NAMES[m], protocol=proto, commands=r['commands'], burst_scans=r['burst_scans'],
                              rpcs=rpc, host_seconds=round(rpc * RPC_MS / 1000, 2)))

    t['area'] = [dict(version=s['name'], commit=s['commit'], standard_mm2=round(s['standard_cell_area_um2'] / 1e6, 3),
                      total_mm2=round(s['total_area_um2'] / 1e6, 3)) for s in load('synth_area.json', [])]

    t['layout'] = []
    for name, rtl in (('layout_intermediate.json', 'Intermediate RTL'), ('layout_final.json', 'Freeze RTL')):
        for c in load(name, {}).get('candidates', []):
            chk = c.get('checks') or {}
            t['layout'].append(dict(run=f"{rtl}: {c['name']}", rtl=rtl, layout=c['layout'], utilization=c['utilization'],
                                    hold_target_ns=c.get('hold_target', 0.02),
                                    die_side_um=c['die_side_um'], setup_wns_ns=c['setup_wns_ns'], hold_wns_ns=c['hold_wns_ns'],
                                    pt_setup_wns_ns=c.get('pt_setup_wns_ns'), pt_hold_wns_ns=c.get('pt_hold_wns_ns'),
                                    wire_m=round(c['wire_length_um'] / 1e6, 2), checks_passed=sum(v is True for v in chk.values()),
                                    checks=len(chk), eligible=c['eligible']))

    t['fpga'] = []
    for name, label in (('xc7_v3_board.json', 'Previous (on board)'), ('xc7_latest.json', 'v2 with trace'),
                        ('xc7_latest_opsonly.json', 'v2 per-op summary'), ('xc7_latest_notrace.json', 'v2 FPGA default')):
        x = load(name)
        if x:
            t['fpga'].append(dict(build=label, lut=x['lut'], lut_share=round(x['lut'] / 20800, 4), ff=x['ff'],
                                  bram36=x['bram36'], dsp=x['dsp']))

    so = load('signoff.json')
    if so:
        t['signoff'] = [dict(check=k, passed=v['pass'], detail=v.get('detail', '')) for k, v in so['checks'].items()]

    OUT.mkdir(exist_ok=True)
    for k, rows in t.items():
        (OUT / f'{k}.json').write_text(json.dumps(rows, indent=1) + '\n')
        print(k, len(rows))


if __name__ == '__main__':
    main()
