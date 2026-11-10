#!/usr/bin/env python3
"""Real-board model deployment -> JTAG inference -> readback timing and golden check.

Requires an already qualified/programmed board and exclusive OpenOCD access.
Never resets, programs or retries a failed operation automatically.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'software/host'))
from nocpu_jtag import JtagTransport, Npu, TclRpc
from npu_program import Package, compile_model, encode_tensor
from npu_e2e import PhaseTimer, TimedTransport, measure


def sha(data):
    return hashlib.sha256(data).hexdigest()


def cases(args):
    if args.package:
        if not args.input or not args.expected:
            raise ValueError('--package requires --input and --expected')
        yield args.package.name, Package.load(args.package), args.input.read_bytes(), args.expected.read_bytes()
    else:
        if args.input or args.expected:
            raise ValueError('--input/--expected require --package')
        names = args.models.split(',') if args.models != 'all' else sorted(p.name for p in args.runs.iterdir() if (p/'model.json').is_file())
        if not names:
            raise ValueError('no models selected')
        for name in names:
            folder = args.runs / name
            model = json.loads((folder/'model.json').read_text())
            frame = json.loads((folder/'golden.json').read_text())['frames'][args.frame_index]
            pkg = compile_model(model, cols=8)
            yield name, pkg, encode_tensor(frame['input'], pkg.manifest['input_dtype']), bytes.fromhex(frame['output_hex'])


def benchmark(npu, timer, pkg, frame, expected, repeat, mhz, out, record, timeout=30):
    if len(frame) != pkg.manifest['input_bytes'] or len(expected) != pkg.manifest['output_bytes']:
        raise ValueError('input/golden length differs from manifest')
    pkg.save(out/'package')
    (out/'input.bin').write_bytes(frame)
    (out/'expected.bin').write_bytes(expected)
    record.update(model_sha256=sha(pkg.image), input_sha256=sha(frame), expected_sha256=sha(expected),
                  model_bytes=len(pkg.image), input_bytes=len(frame), output_bytes=len(expected), runs=[])
    deploy_started = time.perf_counter_ns()
    _, record['deployment'] = measure(timer, 'model_capabilities', lambda: npu.load_model(pkg))
    for index in range(repeat):
        row = dict(index=index, mode='first_after_load' if index == 0 else 'resident_repeat')
        record['runs'].append(row)
        actual, row['timing'] = measure(timer, 'input_metadata', lambda: npu.infer(frame, timeout=timeout))
        if index == 0:
            record['deployment_to_first_output_and_ack_ms'] = (time.perf_counter_ns() - deploy_started)/1e6
        (out/f'actual_{index:04d}.bin').write_bytes(actual)
        row.update(matches_expected=actual == expected, actual_sha256=sha(actual))
        # infer has already ACKed; counters remain readable. Diagnostic RPCs are
        # outside every measured inference/deployment span.
        if not row['matches_expected']:
            raise RuntimeError(f'golden mismatch on run {index}; stopped without retry')
        perf = npu.performance()
        row.update(performance=perf, compute_ms_from_cycles=perf['active_cycles']/(mhz*1000))
    record['deployment_plus_first_inference_ms'] = record['deployment']['total_wall_ms'] + record['runs'][0]['timing']['total_wall_ms']
    times = [r['timing']['total_wall_ms'] for r in record['runs'][1:]]
    record['resident_median_ms'] = statistics.median(times) if times else None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--models', help='comma-separated zoo names, or all')
    source.add_argument('--package', type=Path)
    p.add_argument('--runs', type=Path, default=ROOT/'software/zoo/pretrained')
    p.add_argument('--input', type=Path); p.add_argument('--expected', type=Path)
    p.add_argument('--frame-index', type=int, default=0)
    p.add_argument('--repeat', type=int, default=3)
    p.add_argument('--system-mhz', type=float, required=True, help='assumed board clock, not measured by this script')
    p.add_argument('--host', default='127.0.0.1'); p.add_argument('--port', type=int, default=6666)
    p.add_argument('--timeout', type=float, default=30)
    p.add_argument('--legacy-rpc', action='store_true')
    p.add_argument('--wordwise', action='store_true', help='disable burst data transfers')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.repeat < 1 or args.frame_index < 0 or not math.isfinite(args.system_mhz) or args.system_mhz <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        p.error('repeat/clock/timeout must be positive; frame index must be nonnegative')
    args.output.mkdir(parents=True, exist_ok=False)
    timer = PhaseTimer(); rpc = None
    report = dict(passed=False, started_utc=datetime.now(timezone.utc).isoformat(),
                  scope='host wall time through real JTAG; includes RPC/USB/polling, excludes file IO, golden comparison and performance reads',
                  programming='preprogrammed board; file/configuration identity requires separate programming evidence',
                  system_mhz_assumed=args.system_mhz, burst=not args.wordwise, batch_rpc=not args.legacy_rpc, models=[])
    try:
        # Compile and validate local inputs before touching hardware.
        selected = list(cases(args))
        for _, pkg, frame, expected in selected:
            if len(frame) != pkg.manifest['input_bytes'] or len(expected) != pkg.manifest['output_bytes']:
                raise ValueError('input/golden length differs from manifest')
        rpc = TclRpc(args.host, args.port, timeout=args.timeout)
        report['adapter_speed_query'] = rpc.execute('adapter speed')
        raw = JtagTransport(rpc, timeout=args.timeout, batch_rpc=not args.legacy_rpc)
        npu = Npu(TimedTransport(raw, timer, args.wordwise))
        report['abi'] = npu.read(0); report['array'] = npu.read(0x58)
        report['initial_status'] = npu.status()
        if report['array'] != 0x80008 or any(report['initial_status'][k] for k in ('busy', 'done', 'faulted', 'model_ready', 'input_ready')):
            raise RuntimeError('requires idle, clear, unconfigured ABI 5 / 8x8 board; inspect status before manually resetting')
        for index, (name, pkg, frame, expected) in enumerate(selected):
            out = args.output/f'model_{index:02d}'; out.mkdir()
            record = dict(model=name); report['models'].append(record)
            benchmark(npu, timer, pkg, frame, expected, args.repeat, args.system_mhz, out, record, args.timeout)
        report['passed'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        if hasattr(timer, 'failed'):
            report['failed_operation_timing'] = timer.failed
        raise
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        with (args.output/'phases.csv').open('w', newline='') as f:
            w = csv.writer(f); w.writerow(['model', 'run', 'phase', 'wall_ms'])
            for model in report['models']:
                timings = [('deployment', model.get('deployment', {}))] + [(r['index'], r.get('timing', {})) for r in model.get('runs', [])]
                for index, timing in timings:
                    for phase in timing.get('phases', []):
                        w.writerow([model['model'], index, phase['phase'], phase['wall_ms']])
        if rpc is not None:
            rpc.close()
    print(f'PASS: {len(report["models"])} models; report: {args.output / "report.json"}')


if __name__ == '__main__':
    main()
