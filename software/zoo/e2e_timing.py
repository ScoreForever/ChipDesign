#!/usr/bin/env python3
"""End-to-end JTAG timing breakdown on the RTL (Icarus, physical JTAG pins).

Each zoo model runs the host flow step by step through the real driver
(nocpu_jtag.Npu) recorded by run_program_regression.Recorder, with a phase
marker before each step:

  0 capability checks   1 model upload (config + image)   2 model commit/validate
  3 input upload        4 input commit + START             5 compute (wait for IRQ)
  6 status poll         7 output readout                   8 ACK

The testbench runs TCK at --tck-ns (1000 = 1 MHz) and reports TCK counts and
simulated time per phase. Two host protocols are compared: burst (IR 13h/14h)
and legacy (one 72-bit command plus polls per word). Compute is reported in
NPU cycles (counters); wall time assumes a 50 MHz core.

Host software overhead is not in the RTL: the earlier board run measured a
median 4.0 ms per OpenOCD drscan RPC; the report applies that per RPC.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'software/host'), str(ROOT / 'hardware/npu/scripts'), str(ROOT / 'software')]
import run_program_regression as R  # noqa: E402
from nocpu_jtag import Npu, Op  # noqa: E402
from npu_program import compile_model, reference, encode_tensor  # noqa: E402

PHASES = ['capabilities', 'model_upload', 'model_commit', 'input_upload', 'start', 'compute',
          'status_poll', 'output_readout', 'ack']


class LegacyRecorder(R.Recorder):
    """Same recorder without burst methods: the driver falls back to one
    command per word (the ABI 3 host protocol)."""
    burst_write = None
    burst_read = None

    def __getattribute__(self, name):
        if name in ('burst_write', 'burst_read'):
            raise AttributeError(name)
        return super().__getattribute__(name)


def run_case(out, image, name, model, frame, burst, tck_ns):
    pkg = compile_model(model, cols=8)
    expected = reference(model, frame)[model['nodes'][-1]['output']]
    output = encode_tensor(expected, pkg.manifest['output_dtype'])
    rec = (R.Recorder if burst else LegacyRecorder)(pkg, output)
    host = Npu(rec)
    mark = lambda i: rec.rows.append(f'f2 0 {i:08x} 0 0 0')
    m = pkg.manifest
    mark(0)
    caps = {r: host.read(r) for r in (0x50, 0x54, 0x58, 0x5c, 0x60, 0x6c)}
    mark(1)
    host.command(Op.MODEL_BEGIN)
    for reg, key in zip(range(0x10, 0x28, 4), ('model_bytes', 'op_count', 'input_base', 'input_bytes', 'output_base', 'output_bytes')):
        host.write(reg, m[key])
    host.write(0x64, int(m['input_dtype'] == 'i32'))
    host.write_block(0x10000, [int.from_bytes(pkg.image[o:o + 4], 'little') for o in range(0, len(pkg.image), 4)])
    mark(2)
    host.command(Op.MODEL_COMMIT)
    host.manifest = dict(m)
    frame_bytes = encode_tensor(frame, m['input_dtype'])
    mark(3)
    host.command(Op.INPUT_BEGIN)
    host.write_block(0x20000 + m['input_base'], [int.from_bytes(frame_bytes[o:o + 4].ljust(4, b'\0'), 'little')
                                                  for o in range(0, len(frame_bytes), 4)])
    mark(4)
    host.command(Op.INPUT_COMMIT)
    host.command(Op.START)
    mark(5)
    status = host.read(4)  # the recorder turns this into "wait for IRQ", then the status read
    mark(6)
    base, length = host.read(0x20), host.read(0x24)
    mark(7)
    result = b''.join(v.to_bytes(4, 'little') for v in host.read_block(0x20000 + base, (length + 3) // 4))[:length]
    mark(8)
    host.command(Op.ACK)
    rec.rows.extend(['01 00000038 0 0 0 0'])
    mark(9)
    assert result == output and status & 8
    case = out / f'{name}_{"burst" if burst else "legacy"}'
    case.mkdir(parents=True, exist_ok=True)
    trace = case / 'commands.txt'
    trace.write_text('\n'.join(rec.rows) + '\n')
    import subprocess
    p = subprocess.run(['vvp', str(image), f'+TRACE={trace.relative_to(ROOT).as_posix()}', f'+TCK_NS={tck_ns}'],
                       cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=6 * 3600)
    (case / 'rtl.log').write_text(p.stdout)
    log = p.stdout
    if p.returncode or 'FATAL:' in log or 'PASS program' not in log:
        raise RuntimeError(f'{case}: {log[-2000:]}')
    marks = [(int(a), int(t), int(k)) for a, t, k in re.findall(r'PHASE (\d+) time_ns=(\d+) ticks=(\d+)', log)]
    cycles = [int(v) for a, v in re.findall(r'METRIC reg=([0-9a-f]+) value=(\d+)', log) if int(a, 16) == 0x38][-1]
    phases = {}
    for (i, t0, k0), (_, t1, k1) in zip(marks, marks[1:]):
        phases[PHASES[i]] = dict(tck=k1 - k0, sim_ns=t1 - t0)
    scans = sum(1 for r in rec.rows if r[:2] in ('80', '81')) if burst else 0
    commands = sum(1 for r in rec.rows if r[:2] not in ('f0', 'f1', 'f2', '80', '81'))
    return dict(model=name, protocol='burst' if burst else 'legacy', tck_ns=tck_ns, compute_cycles=cycles,
                model_bytes=m['model_bytes'], input_bytes=m['input_bytes'], output_bytes=m['output_bytes'],
                commands=commands, burst_scans=scans, phases=phases, total_tck=marks[-1][2] - marks[0][2])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runs', type=Path, required=True, help='zoo run directories (model.json, test_frames.pt)')
    ap.add_argument('--models', required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--tck-ns', type=float, default=1000)
    ap.add_argument('--jobs', type=int, default=16)
    args = ap.parse_args()
    import torch
    from concurrent.futures import ThreadPoolExecutor
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sources = (ROOT / 'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
    image = out / 'program_jtag.vvp'
    R.run(['iverilog', '-g2012', '-s', 'tb_npu_program', '-o', image, '-DJTAG_TEST', *sources,
           'hardware/soc/sim/tb/tb_npu_program.sv'], out / 'compile.log')
    jobs = []
    for spec in args.models.split(','):
        run_dir = args.runs / spec if (args.runs / spec / 'model.json').exists() else Path(spec)
        model = json.loads((run_dir / 'model.json').read_text())
        frame = torch.load(run_dir / 'test_frames.pt')['frames'][0].flatten().tolist()
        for burst in (True, False):
            jobs.append((run_dir.name, model, frame, burst))
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(lambda j: run_case(out, image, j[0], j[1], j[2], j[3], args.tck_ns), jobs))
    (out / 'e2e.json').write_text(json.dumps(rows, indent=1) + '\n')
    for r in rows:
        print(r['model'], r['protocol'], 'total TCK', r['total_tck'], 'compute cycles', r['compute_cycles'],
              {k: v['tck'] for k, v in r['phases'].items()})


if __name__ == '__main__':
    main()
