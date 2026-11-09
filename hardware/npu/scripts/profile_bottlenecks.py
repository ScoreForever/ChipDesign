#!/usr/bin/env python3
"""Profile the ABI 5.0 8x8 NPU with checked RTL runs and tile/workload sweeps.

No production RTL is edited. Tile variants are simulation experiments, not
routed or board-qualified implementations. Wait counters overlap except for
the explicitly partitioned MAC issue/wait/prepare buckets.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'hardware/npu/scripts'))
import run_program_regression as regression
import sim_build

COUNTERS = ('active_cycles model_requests model_stall_cycles model_max_stall '
            'model_responses model_wait_response_cycles activation_requests '
            'activation_stall_cycles activation_max_stall activation_responses '
            'activation_wait_response_cycles writes mac_issues mac_wait_activation_cycles '
            'mac_wait_weight_cycles mac_wait_slot_cycles kernel_prepare_cycles '
            'result_slots_peak weight_banks_peak inflight_peak post_lanes '
            'gather_contiguous_groups gather_scalar_groups gather_zero_groups').split()


def workloads():
    for path in sorted((ROOT / 'software/zoo/pretrained').glob('*/model.json')):
        yield 'packaged_' + path.parent.name, json.loads(path.read_text()), json.loads(
            (path.parent / 'golden.json').read_text())['frames'][0]['input'], True
    # Fixed 8x8 spatial extent: separately vary K/channel packing and OC tails.
    for ic, oc in [(1, 8), (7, 8), (8, 8), (9, 8), (16, 8), (8, 1), (8, 7), (8, 9), (8, 16)]:
        model = dict(input=dict(name='x', shape=[8, 8, ic], dtype='i4'), nodes=[
            dict(op='Conv', input='x', output='a', kernel=1, channels=oc,
                 weights=[i % 15 - 7 for i in range(ic * oc)], bias=[0] * oc),
            dict(op='Requant', input='a', output='y', multiplier=[0x4000] * oc, shift=[-3] * oc)])
        frame = [i % 16 - 8 for i in range(64 * ic)]
        yield f'conv_ic{ic}_oc{oc}', model, frame, True
    selected = {'cnn', 'slot_pressure', 'strided', 'dwconv', 'gap_head'}
    for name, model, frame in regression.fixtures():
        if name in selected:
            for fused in (True, False):
                yield name, model, frame, fused



def limit_workloads():
    """Independent saturation axes, using the full compiled/checked datapath."""
    def conv(name, positions, ic, oc, quant=True):
        nodes = [dict(op='Conv', input='x', output='a', kernel=1, channels=oc,
                      weights=[i % 15 - 7 for i in range(ic * oc)], bias=[0] * oc)]
        if quant:
            nodes.append(dict(op='Requant', input='a', output='y',
                              multiplier=[0x4000] * oc, shift=[-8] * oc))
        return name, dict(input=dict(name='x', shape=[positions // 32, 32, ic] if positions > 255 else [1, positions, ic], dtype='i4'),
                          nodes=nodes), [i % 16 - 8 for i in range(positions * ic)], True
    for k in (1, 7, 8, 9, 16, 64, 256, 1024):
        yield conv(f'limit_k{k}', 1, k, 8)
    for positions in (1, 4, 16, 64, 256, 1024):
        yield conv(f'limit_reuse{positions}', positions, 8, 8)
    for oc in (1, 7, 8, 9, 16, 32, 64, 128, 256):
        yield conv(f'limit_oc{oc}', 16, 8, oc)
    for k, positions in ((64, 16), (64, 64), (64, 128), (256, 16), (256, 32)):
        yield conv(f'limit_compute_k{k}_p{positions}', positions, k, 8)
    for k in (1, 8, 64):
        for quant in (False, True):
            yield conv(f'limit_post_k{k}_q{int(quant)}', 64, k, 8, quant)
    for name, model, frame in regression.fixtures():
        if name in ('descriptors_17', 'descriptors_64', 'descriptors_128',
                    'full_activation', 'full_activation_8k', 'max_k_int4'):
            yield name, model, frame, True


def annotate(row, model, frame):
    p = dict(zip(COUNTERS, row['performance'][0]))
    # Exclusive by core perf_mac priority; other includes descriptor setup,
    # standalone ops and pipeline drains. It is NOT a separate hardware counter.
    accounted = sum(p[k] for k in ('mac_issues', 'mac_wait_activation_cycles',
                    'mac_wait_weight_cycles', 'mac_wait_slot_cycles', 'kernel_prepare_cycles'))
    p['other_cycles'] = p['active_cycles'] - accounted
    assert p['other_cycles'] >= 0
    logical = regression.reference(model, frame)
    useful = 0
    for n in model['nodes']:
        if n['op'] in ('Conv', 'FC'):
            output_elements = len(logical[n['output']])
            # There is one logical MAC per output element per weight of its channel.
            def count(v):
                return sum(map(count, v)) if isinstance(v, list) else 1
            useful += output_elements * count(n['weights']) // n['channels']
    p['useful_macs'] = useful
    p['issue_duty_pct'] = 100 * p['mac_issues'] / p['active_cycles']
    p['useful_array_util_pct'] = 100 * useful / (64 * p['active_cycles'])
    p['logical_work_per_issued_capacity_pct'] = 100 * useful / (64 * p['mac_issues']) if p['mac_issues'] else 0
    # Logical work counts padding zeros; this is not a count of nonzero MACs.
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--tiles', type=int, nargs='+', choices=[8, 16], default=[16, 8])
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--memory', choices=sorted(sim_build.MEMORY), default='fpga_mem',
                    help='memory configuration (see sim_build.py)')
    ap.add_argument('--limits-only', action='store_true', help='saturate K, reuse, OC, postprocessing and descriptors')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources = sim_build.watched_sources(args.memory)
    before = {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in sources}
    cases = list(limit_workloads() if args.limits_only else workloads())
    for name, model, frame, fused in cases:
        regression.compile_model(model, cols=8, fuse=fused)
    results = []
    for tile in args.tiles:
        folder = out / f'tile{tile}'
        folder.mkdir()
        image = folder / 'program.vvp'
        sim_build.compile_program_tb(image, folder / 'compile.log', memory=args.memory, tile=tile)
        def measure(case):
            name, model, frame, fused = case
            row = regression.simulate(folder, image, name, model, frame, 8, fused, False, True)
            row.update(tile=tile, fused=fused, directory=str(folder / row['case']))
            row['metrics'] = annotate(row, model, frame)
            print(f'PASS tile={tile} {row["case"]}: {row["cycles"]}', flush=True)
            return row
        with ThreadPoolExecutor(args.jobs) as pool:
            results.extend(pool.map(measure, cases))
    assert before == {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in sources}, 'RTL changed during measurement'
    summary = dict(sources_sha256=before, tests=results, clock_mhz_assumption=50,
                   scope='RTL experiment; two repeats, every materialized tensor write checked; logical MACs include padding zeros')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    with (out / 'metrics.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['case', 'tile', 'fused', *results[0]['metrics']])
        writer.writeheader()
        writer.writerows(dict(case=r['case'], tile=r['tile'], fused=r['fused'], **r['metrics']) for r in results)
    print(f'ALL {len(results)} CHECKED PERFORMANCE CASES PASSED', flush=True)


if __name__ == '__main__':
    main()
