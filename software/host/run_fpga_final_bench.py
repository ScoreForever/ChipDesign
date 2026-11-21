#!/usr/bin/env python3
"""Final FPGA benchmark: 7 zoo models, 6 demo scenes and tiled ResNet-20/18.

Backends:
  mpsse    FT232H MPSSE, one run per --tck-mhz-list entry (tck_khz = MHz*1000)
  openocd  OpenOCD Tcl RPC; TCK comes from the OpenOCD config, so one run
  mock     in-process mailbox model executing npu_program.reference; no
           cycles are reported and receipts are never fpga_validated
  rtl      Icarus audit of the 7 zoo models (golden vectors through
           tb_npu_program, every tensor write checked, NPU_CYCLES from 0x38)

  python software/host/run_fpga_final_bench.py --backend mpsse --out-dir /tmp/fb
  python software/host/run_fpga_final_bench.py --backend rtl --out-dir /tmp/fb \
      --audit-json /tmp/w4_resnet_ready/model_audit.json

Exit code is nonzero on any golden mismatch or suite error.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import time
from types import SimpleNamespace

HOST = Path(__file__).resolve().parent
ROOT = HOST.parents[1]
for _p in (HOST, ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from nocpu_jtag import ABI, NUMERIC_FORMAT, Npu, Op, create_transport  # noqa: E402
from npu_program import Package, decode_tensor, encode_tensor, reference  # noqa: E402

ZOO = ROOT / 'software/zoo/pretrained'
MODELS = ('ecg', 'har', 'mnist_l', 'fashion_cnn', 'kws_m', 'kws_l', 'gtsrb_l')
SCENES = ('mnist', 'kws', 'ecg', 'har', 'face', 'emotion')
RESNET_DEFAULTS = {
    'resnet20': ('/tmp/w4_resnet_ready/resnet20/tiled_plan_opt', '/tmp/w4_resnet_ready/resnet20/test_samples_10.npz'),
    'resnet18': ('/tmp/w4_resnet_ready/resnet18/tiled_plan_opt', '/tmp/w4_resnet_ready/resnet18/test_samples_5.npz'),
}
SCOPE_NOTE = (
    'Representative frames only: these are the published golden inputs of the six demo scenes '
    '(mnist_l/kws_l/ecg/har 16 each, face/emotion 8 each) run on the selected backend. The full offline '
    'checks are 80/80 demo-scene golden outputs (software/host/demos/README.md, `demo.py --self-test`; '
    'docs/DEMO_SOFTWARE_20261116.md) and 112/112 zoo golden frames (7 models x 16, '
    'software/zoo/pretrained/MANIFEST.json golden_frames; docs/DEMO_SOFTWARE_20261116.md). Both counts are '
    'output-equals-golden checks, not label accuracy.')


def model_output_name(model: dict) -> str:
    return model.get('output') or model['nodes'][-1]['output']


def load_zoo_model(name: str, root: Path = ZOO):
    """(package, model dict, golden dict) for a pretrained zoo/demo model directory."""
    directory = root / name
    return (Package.load(directory / 'c8'),
            json.loads((directory / 'model.json').read_text(encoding='utf-8')),
            json.loads((directory / 'golden.json').read_text(encoding='utf-8')))


# --------------------------------------------------------------------------- mock transport
class MockNpuTransport:
    """In-process NPU mailbox (nocpu_jtag.Op protocol) that runs npu_program.reference.

    Images must be registered with their logical model first, since a package
    image alone does not carry the reference graph. Register 0x38 reads 0: the
    mock has no cycle model and callers must report cycles as unavailable.
    """
    supports_pipeline_rpc = False
    is_mock = True

    def __init__(self, cols: int = 8, corrupt_outputs: bool = False) -> None:
        self.regs = {0: ABI, 0x50: 16384, 0x54: 8192, 0x58: (8 << 16) | cols,
                     0x5c: 1024, 0x60: 128, 0x6c: NUMERIC_FORMAT}
        self.model_mem = bytearray(self.regs[0x54])
        self.act_mem = bytearray(self.regs[0x50])
        self.registry: dict[tuple, tuple[dict, dict]] = {}
        self.active = None
        self.model_valid = self.done = False
        self.burst_addr = 0
        self.corrupt_outputs = corrupt_outputs
        self.commands = 0

    @staticmethod
    def _key(image: bytes, input_bytes: int, output_bytes: int) -> tuple:
        return hashlib.sha256(bytes(image)).hexdigest(), int(input_bytes), int(output_bytes)

    def register(self, package, model: dict) -> None:
        m = package.manifest
        self.registry[self._key(package.image, m['input_bytes'], m['output_bytes'])] = (model, dict(m))

    def _mem(self, addr):
        if 0x10000 <= addr < 0x10000 + len(self.model_mem):
            return self.model_mem, addr - 0x10000
        if 0x20000 <= addr < 0x20000 + len(self.act_mem):
            return self.act_mem, addr - 0x20000
        return None, None

    def _read(self, addr):
        if addr == 4:
            return (2 if self.model_valid else 0) | (8 if self.done else 0)
        mem, at = self._mem(addr)
        if mem is not None:
            return int.from_bytes(mem[at:at + 4], 'little')
        return self.regs.get(addr, 0)

    def _write(self, addr, data):
        mem, at = self._mem(addr)
        if mem is not None:
            mem[at:at + 4] = (int(data) & 0xffffffff).to_bytes(4, 'little')
        elif addr < 0x10000:
            self.regs[addr] = int(data) & 0xffffffff

    def _run(self):
        model, m = self.active
        base, length = self.regs[0x18], self.regs[0x1c]
        raw_in = bytes(self.act_mem[base:base + length])
        if 'input2' in model or len(m.get('input_tensors', [])) > 1:
            from software.compiler.runtime import decode_tile_inputs, encode_tensor_np, execute_sub_model
            tiles = decode_tile_inputs(SimpleNamespace(sub_model=model), raw_in)
            out_vals = execute_sub_model(model, tiles)[model_output_name(model)]
            out = encode_tensor_np(out_vals, m['output_dtype'])
        else:
            values = decode_tensor(raw_in, m['input_dtype'], m['input_elements'])
            out = encode_tensor(reference(model, values)[model_output_name(model)], m['output_dtype'])
        if self.corrupt_outputs:
            out = bytes([out[0] ^ 1]) + out[1:]
        obase = self.regs[0x20]
        self.act_mem[obase:obase + len(out)] = out
        self.regs[0x2c] = self.regs.get(0x2c, 0) + 1
        self.done = True

    def exchange(self, op, addr=0, data=0):
        self.commands += 1
        if op == Op.READ:
            self.burst_addr = addr + 4
            return self._read(addr)
        if op == Op.WRITE:
            self.burst_addr = addr + 4
            self._write(addr, data)
            return 0
        if op == Op.MODEL_BEGIN:
            self.model_valid = self.done = False
            self.active = None
        elif op == Op.MODEL_COMMIT:
            image = bytes(self.model_mem[:self.regs[0x10]])
            entry = self.registry.get(self._key(image, self.regs[0x1c], self.regs[0x24]))
            if entry is None:
                raise RuntimeError('mock NPU: model image was not registered with its reference graph')
            self.active, self.model_valid = entry, True
        elif op == Op.START:
            if not self.model_valid:
                raise RuntimeError('mock NPU: START without a committed model')
            self._run()
            return 1
        elif op == Op.ACK:
            self.done = False
        elif op == Op.RESET:
            self.model_valid = self.done = False
            self.active = None
        return 0

    def burst_write(self, words):
        for word in words:
            self._write(self.burst_addr, word)
            self.burst_addr += 4

    def burst_read(self, count):
        values = []
        for _ in range(count):
            values.append(self._read(self.burst_addr))
            self.burst_addr += 4
        return values

    def close(self):
        pass


def make_mock_transport() -> MockNpuTransport:
    return MockNpuTransport()


def register_mock(transport, package, model) -> None:
    if getattr(transport, 'is_mock', False):
        transport.register(package, model)


# --------------------------------------------------------------------------- transport selection
def transport_runs(args):
    """[(label, tck_mhz or None, tck_note)] for the selected backend."""
    if args.backend == 'mpsse':
        return [(f'tck_{mhz:g}mhz', mhz, f'MPSSE TCK {mhz:g} MHz (tck_khz={int(round(mhz * 1000))})')
                for mhz in parse_tck_list(args.tck_mhz_list)]
    if args.backend == 'openocd':
        return [('openocd', None, 'TCK set by the OpenOCD adapter config (adapter speed); single run, '
                 '--tck-mhz-list ignored')]
    return [('mock', None, 'mock backend: no JTAG link, TCK not applicable')]


def parse_tck_list(text: str) -> list[float]:
    values = [float(v) for v in str(text).split(',') if v.strip()]
    if not values or any(v <= 0 for v in values):
        raise ValueError('--tck-mhz-list needs positive MHz values')
    return values


def open_transport(args, tck_mhz):
    """(transport, closer). Hardware backends go through nocpu_jtag.create_transport."""
    if args.backend == 'mock':
        transport = make_mock_transport()
        return transport, transport.close
    kwargs = dict(host=args.host, port=args.port, ftdi_url=args.ftdi_url, sys_clk_mhz=args.sys_clk_mhz)
    if args.backend == 'mpsse':
        kwargs['tck_khz'] = int(round(tck_mhz * 1000))
    return create_transport(args.backend, **kwargs)


def is_board(args) -> bool:
    return args.backend in ('mpsse', 'openocd')


def real_board(args, transport) -> bool:
    """True only for a hardware backend whose transport is not the in-process mock."""
    return is_board(args) and not getattr(transport, 'is_mock', False)


def read_cycles(npu, real):
    """Completed-job NPU_CYCLES (reg 0x38); None on the mock, which has no cycle model."""
    return int(npu.read(0x38)) if real else None


def latency_stats(values_ms):
    if not values_ms:
        return None
    ordered = sorted(values_ms)
    p95 = ordered[min(len(ordered) - 1, max(0, int(round(0.95 * len(ordered))) - 1))]
    mean = statistics.fmean(ordered)
    return dict(count=len(ordered), median_ms=statistics.median(ordered), mean_ms=mean, p95_ms=p95,
                min_ms=ordered[0], max_ms=ordered[-1], fps=(1000.0 / mean) if mean > 0 else None)


def frame_cycle(frames, count):
    """`count` frames cycling through the golden list (count None = all once)."""
    if count is None or count <= 0:
        return list(frames)
    return [frames[i % len(frames)] for i in range(count)]


# --------------------------------------------------------------------------- models suite
def bench_model(npu, transport, args, name, root=ZOO):
    real = real_board(args, transport)
    package, model, golden = load_zoo_model(name, root)
    register_mock(transport, package, model)
    m = package.manifest
    frames = frame_cycle(golden['frames'], args.max_frames)
    mismatches, cycles, warm_ms = [], [], []

    def run_one(frame):
        raw = npu.infer(encode_tensor(frame['input'], m['input_dtype']))
        return decode_tensor(raw, m['output_dtype'], m['output_elements'])

    t0 = time.perf_counter()
    npu.load_model(package)
    t1 = time.perf_counter()
    first = run_one(frames[0])
    t2 = time.perf_counter()
    if list(first) != list(frames[0]['output']):
        mismatches.append(dict(index=frames[0]['index'], phase='cold'))
    cold_cycles = read_cycles(npu, real)
    for frame in frames:
        start = time.perf_counter()
        out = run_one(frame)
        warm_ms.append((time.perf_counter() - start) * 1000)
        cyc = read_cycles(npu, real)
        if cyc is not None:
            cycles.append(cyc)
        if list(out) != list(frame['output']):
            mismatches.append(dict(index=frame['index'], phase='warm'))
    checked = len(frames) + 1
    return dict(
        model=name, model_bytes=m['model_bytes'], input_bytes=m['input_bytes'], output_bytes=m['output_bytes'],
        ops=m['op_count'], frames_checked=checked, golden_match=not mismatches, mismatches=mismatches,
        cold=dict(load_model_ms=(t1 - t0) * 1000, first_infer_ms=(t2 - t1) * 1000, total_ms=(t2 - t0) * 1000,
                  npu_cycles=cold_cycles),
        warm=latency_stats(warm_ms),
        npu_cycles=(dict(values=sorted(set(cycles)), median=statistics.median(cycles),
                         compute_us=statistics.median(cycles) / args.sys_clk_mhz) if cycles else None),
    )


def suite_models(args, models=MODELS):
    runs = []
    for label, tck_mhz, note in transport_runs(args):
        entry = dict(label=label, tck_mhz=tck_mhz, tck_note=note, models={}, errors=[])
        try:
            transport, closer = open_transport(args, tck_mhz)
        except Exception as exc:  # board absent, adapter busy, ...
            entry['errors'].append(f'connect: {type(exc).__name__}: {exc}')
            runs.append(entry)
            continue
        entry['real_board'] = real_board(args, transport)
        try:
            npu = Npu(transport)
            for name in models:
                try:
                    entry['models'][name] = bench_model(npu, transport, args, name)
                    print(f"[models {label}] {name}: golden_match={entry['models'][name]['golden_match']}", flush=True)
                except Exception as exc:
                    entry['errors'].append(f'{name}: {type(exc).__name__}: {exc}')
        except Exception as exc:
            entry['errors'].append(f'{type(exc).__name__}: {exc}')
        finally:
            closer()
        runs.append(entry)
    rows = [r for run in runs for r in run['models'].values()]
    errors = [e for run in runs for e in run['errors']]
    passed = bool(rows) and not errors and all(r['golden_match'] for r in rows) and \
        all(len(run['models']) == len(models) for run in runs)
    board = bool(runs) and all(run.get('real_board') for run in runs)
    return dict(suite='models', backend=args.backend, sys_clk_mhz=args.sys_clk_mhz,
                fpga_available=board and bool(rows),
                fpga_validated=board and passed,
                cycles_note=None if board else 'no real board: mock has no cycle model; npu_cycles is null',
                passed=passed, runs=runs)


# --------------------------------------------------------------------------- scenes suite
def scene_session(args, transport, scene):
    """Demo InferenceSession (demos.runner.make_session) wired to an already-open transport.

    The session's own infer()/timing code is reused; only connect() is
    replaced so the transport honours --sys-clk-mhz / the mock backend.
    """
    from demos.runner import make_session
    from npu_e2e import TimedTransport
    session_args = SimpleNamespace(backend='fpga', host=args.host, port=args.port, log=None,
                                   vision_models=None)
    session = make_session(session_args, scene)
    model = json.loads((session.directory / 'model.json').read_text(encoding='utf-8'))
    register_mock(transport, session.package, model)
    session.npu = Npu(TimedTransport(transport, session.timer))
    session.npu.load_model(session.package)
    return session


def suite_scenes(args, scenes=SCENES):
    from demos.signals import Sample
    runs = []
    for label, tck_mhz, note in transport_runs(args):
        entry = dict(label=label, tck_mhz=tck_mhz, tck_note=note, scenes={}, errors=[])
        try:
            transport, closer = open_transport(args, tck_mhz)
        except Exception as exc:
            entry['errors'].append(f'connect: {type(exc).__name__}: {exc}')
            runs.append(entry)
            continue
        entry['real_board'] = real = real_board(args, transport)
        try:
            for scene in scenes:
                session = None
                try:
                    session = scene_session(args, transport, scene)
                    golden = json.loads((session.directory / 'golden.json').read_text(encoding='utf-8'))
                    samples = [Sample(str(f['index']), f['input'], None, f.get('label'), f['output'])
                               for f in frame_cycle(golden['frames'], args.max_frames)]
                    exact, latency, transfer, wait, cycles = 0, [], [], [], []
                    for sample in samples:
                        prediction = session.infer(sample.values)
                        exact += list(prediction.scores) == sample.expected_scores
                        latency.append(prediction.elapsed_ms)
                        if prediction.transfer_ms is not None:
                            transfer.append(prediction.transfer_ms)
                            wait.append(prediction.compute_wait_ms)
                        if real and prediction.cycles is not None:
                            cycles.append(int(prediction.cycles))
                    entry['scenes'][scene] = dict(
                        model=session.name, frames=len(samples), golden_exact=exact,
                        agreement_rate=exact / len(samples), latency=latency_stats(latency),
                        transfer_ms_median=statistics.median(transfer) if transfer else None,
                        start_wait_ms_median=statistics.median(wait) if wait else None,
                        npu_cycles_median=statistics.median(cycles) if cycles else None)
                    print(f'[scenes {label}] {scene}: {exact}/{len(samples)} exact', flush=True)
                except Exception as exc:
                    entry['errors'].append(f'{scene}: {type(exc).__name__}: {exc}')
                finally:
                    if session is not None:
                        session.rpc = None  # transport is owned and closed here
                        session.close()
        finally:
            closer()
        runs.append(entry)
    rows = [r for run in runs for r in run['scenes'].values()]
    frames = sum(r['frames'] for r in rows)
    exact = sum(r['golden_exact'] for r in rows)
    errors = [e for run in runs for e in run['errors']]
    passed = bool(rows) and not errors and exact == frames and all(len(run['scenes']) == len(scenes) for run in runs)
    board = bool(runs) and all(run.get('real_board') for run in runs)
    return dict(suite='scenes', backend=args.backend, sys_clk_mhz=args.sys_clk_mhz,
                fpga_available=board and bool(rows),
                fpga_validated=board and passed,
                real_board_frames=frames if board else 0,
                frames_checked=frames, frames_exact=exact,
                agreement_rate=(exact / frames) if frames else None,
                scope_note=SCOPE_NOTE, passed=passed, runs=runs)


# --------------------------------------------------------------------------- resnet suite
def register_plan_mock(transport, plan):
    from software.compiler import ProgramGroup
    for step in plan.steps:
        if isinstance(step, ProgramGroup):
            register_mock(transport, step.package, step.sub_model)


def bench_resnet(args, transport, npu, name, plan_dir, samples_path):
    plan_dir, samples_path = Path(plan_dir), Path(samples_path)
    if not (plan_dir / 'plan.json').is_file():
        return dict(model=name, skipped=f'plan directory missing: {plan_dir}')
    if not samples_path.is_file():
        return dict(model=name, skipped=f'samples file missing: {samples_path}')
    import numpy as np
    from software.compiler import NpuBackend, TiledPlan, TiledRuntime, oracle_reference
    real = real_board(args, transport)
    plan = TiledPlan.load(plan_dir)
    register_plan_mock(transport, plan)
    with np.load(samples_path) as blob:
        inputs = np.asarray(blob['inputs'])
        labels = np.asarray(blob['labels']) if 'labels' in blob else None
    count = len(inputs) if not args.max_frames else min(len(inputs), args.max_frames)
    runtime = TiledRuntime(NpuBackend(npu, read_cycles=real))
    rows = []
    for i in range(count):
        x = np.asarray(inputs[i], dtype=np.int64).reshape(plan.graph.input.shape)
        out, tel = runtime.run(plan, x)
        expected = oracle_reference(plan.graph, x)[plan.graph.output]
        out = np.asarray(out)
        exact = out.shape == np.asarray(expected).shape and bool(np.array_equal(out, expected))
        cycles = int(tel['npu_cycles']) if real else None
        compute_ms = cycles / (args.sys_clk_mhz * 1000.0) if cycles is not None else None
        # Without real cycles the remainder is mock reference time, not JTAG.
        jtag_ms = tel['wall_ms'] - tel['host_ms'] - compute_ms if compute_ms is not None else None
        rows.append(dict(
            index=i, label=int(labels[i]) if labels is not None else None,
            predicted=int(np.argmax(out.reshape(-1))), bit_exact=exact,
            max_abs_diff=(int(np.max(np.abs(out.astype(np.int64) - np.asarray(expected, dtype=np.int64))))
                          if out.shape == np.asarray(expected).shape else None),
            npu_cycles=cycles, wall_ms=tel['wall_ms'], host_ms=tel['host_ms'],
            npu_compute_ms=compute_ms, jtag_transfer_ms=jtag_ms,
            telemetry={k: tel[k] for k in ('model_loads', 'tile_infers', 'jtag_model_bytes',
                                           'jtag_input_bytes', 'jtag_output_bytes')}))
        print(f'[resnet] {name} sample {i}: bit_exact={exact} wall={tel["wall_ms"]:.0f} ms', flush=True)

    def med(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return statistics.median(vals) if vals else None
    return dict(model=name, plan_dir=str(plan_dir), samples=str(samples_path), plan_summary=plan.summary(),
                samples_checked=len(rows), bit_exact=all(r['bit_exact'] for r in rows) and bool(rows),
                label_correct=sum(r['label'] == r['predicted'] for r in rows if r['label'] is not None),
                npu_cycles_median=med('npu_cycles'),
                breakdown_median_ms=dict(wall=med('wall_ms'), host=med('host_ms'), npu_compute=med('npu_compute_ms'),
                                         jtag_transfer=med('jtag_transfer_ms')),
                breakdown_note=('jtag_transfer = wall - host - cycles/sys_clk; it includes driver/USB '
                                'overhead and completion polling, not only shift time'),
                rows=rows)


def suite_resnet(args):
    targets = (('resnet20', args.resnet20_plan, args.resnet20_samples),
               ('resnet18', args.resnet18_plan, args.resnet18_samples))
    runs = []
    for label, tck_mhz, note in transport_runs(args):
        entry = dict(label=label, tck_mhz=tck_mhz, tck_note=note, models={}, errors=[])
        try:
            transport, closer = open_transport(args, tck_mhz)
        except Exception as exc:
            entry['errors'].append(f'connect: {type(exc).__name__}: {exc}')
            runs.append(entry)
            continue
        entry['real_board'] = real_board(args, transport)
        try:
            npu = Npu(transport)
            for name, plan_dir, samples in targets:
                try:
                    entry['models'][name] = bench_resnet(args, transport, npu, name, plan_dir, samples)
                except Exception as exc:
                    entry['errors'].append(f'{name}: {type(exc).__name__}: {exc}')
        finally:
            closer()
        runs.append(entry)
    ran = [r for run in runs for r in run['models'].values() if 'skipped' not in r]
    errors = [e for run in runs for e in run['errors']]
    passed = not errors and all(r['bit_exact'] for r in ran)
    board = bool(runs) and all(run.get('real_board') for run in runs)
    return dict(suite='resnet', backend=args.backend, sys_clk_mhz=args.sys_clk_mhz,
                fpga_available=board and bool(ran),
                fpga_validated=board and bool(ran) and passed,
                skipped=sorted({f"{n}: {r['skipped']}" for run in runs for n, r in run['models'].items()
                                if 'skipped' in r}),
                passed=passed, runs=runs)


# --------------------------------------------------------------------------- RTL audit
def audit_models_rtl(work_dir=None, models=MODELS, frames=None, jobs=None, root=ZOO):
    """Run zoo golden vectors through the Icarus tb_npu_program testbench.

    Uses run_program_regression.simulate (real Npu host driver, every tensor
    write checked against npu_program.reference, cold + warm job per frame) and
    then compares the reference output with the stored golden output. Returns
    {model: {bit_exact, npu_cycles, model_bytes, input_bytes, output_bytes, ops, vectors_checked, ...}}.
    work_dir must be inside the repository (the testbench takes repo-relative paths).
    """
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        raise RuntimeError('iverilog/vvp not on PATH')
    for p in (ROOT / 'hardware/npu/scripts', ROOT / 'software/zoo'):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import run_program_regression as R
    from rtl_check import build
    from npu_program import compile_model
    work = Path(work_dir or ROOT / 'build/final_bench_rtl').resolve()
    work.relative_to(ROOT)
    work.mkdir(parents=True, exist_ok=True)
    image = build(work, 8, [])
    jobs_list, info = [], {}
    for name in models:
        package, model, golden = load_zoo_model(name, root)
        rebuilt = compile_model(model, cols=8)
        info[name] = dict(package=package, image_matches_c8=rebuilt.image == package.image)
        for frame in (golden['frames'] if frames is None else golden['frames'][:frames]):
            jobs_list.append((name, model, frame))

    def one(job):
        name, model, frame = job
        try:
            row = R.simulate(work, image, f"{name}_g{frame['index']}", model, frame['input'], 8, True, False)
        except Exception as exc:
            return name, model, frame, None, f'{type(exc).__name__}: {str(exc)[-500:]}'
        return name, model, frame, row, None

    results = {name: [] for name in models}
    with ThreadPoolExecutor(jobs or min(16, os.cpu_count() or 4)) as pool:
        for name, model, frame, row, error in pool.map(one, jobs_list):
            out = reference(model, frame['input'])[model_output_name(model)]
            results[name].append(dict(index=frame['index'], error=error,
                                      rtl_pass=row is not None, golden_exact=list(out) == list(frame['output']),
                                      cycles=row['cycles'] if row else None))
            print(f"[rtl] {name} #{frame['index']}: {'PASS' if row else 'FAIL'} "
                  f"cycles={row['cycles'] if row else error}", flush=True)
    audit = {}
    for name in models:
        m = info[name]['package'].manifest
        rows = results[name]
        cycles = sorted({c for r in rows if r['cycles'] for c in r['cycles']})
        audit[name] = dict(
            bit_exact=bool(rows) and info[name]['image_matches_c8'] and all(r['rtl_pass'] and r['golden_exact'] for r in rows),
            npu_cycles=cycles[0] if len(cycles) == 1 else (cycles or None),
            model_bytes=m['model_bytes'], input_bytes=m['input_bytes'], output_bytes=m['output_bytes'],
            ops=m['op_count'], vectors_checked=len(rows),
            rtl_jobs=sum(len(r['cycles'] or []) for r in rows),
            image_matches_c8=info[name]['image_matches_c8'],
            failures=[r for r in rows if not (r['rtl_pass'] and r['golden_exact'])])
    return audit


def suite_rtl(args):
    audit = audit_models_rtl(frames=args.max_frames or None)
    git_rev = _git_rev()
    if args.audit_json:
        path = Path(args.audit_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: {f: v[f] for f in ('bit_exact', 'npu_cycles', 'model_bytes', 'input_bytes',
                                                          'output_bytes', 'ops', 'vectors_checked')}
                                    for k, v in audit.items()}, indent=2) + '\n', encoding='utf-8')
    return dict(suite='rtl_audit', backend='rtl', commit=git_rev, fpga_available=False, fpga_validated=False,
                passed=all(v['bit_exact'] for v in audit.values()), models=audit)


def _git_rev():
    import subprocess
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, text=True,
                              capture_output=True, check=True).stdout.strip()
    except Exception:
        return None


# --------------------------------------------------------------------------- CLI
def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--backend', choices=['mpsse', 'openocd', 'mock', 'rtl'], default='mock')
    p.add_argument('--tck-mhz-list', default='1,2,3,5,8,10,15')
    p.add_argument('--sys-clk-mhz', type=float, default=57.143)
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--suite', choices=['all', 'models', 'scenes', 'resnet'], default='all')
    p.add_argument('--max-frames', type=int, default=None,
                   help='frames per model/scene/resnet (default: all golden frames / samples)')
    p.add_argument('--resnet20-plan', default=RESNET_DEFAULTS['resnet20'][0])
    p.add_argument('--resnet18-plan', default=RESNET_DEFAULTS['resnet18'][0])
    p.add_argument('--resnet20-samples', default=RESNET_DEFAULTS['resnet20'][1])
    p.add_argument('--resnet18-samples', default=RESNET_DEFAULTS['resnet18'][1])
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=6666)
    p.add_argument('--ftdi-url', default='ftdi://ftdi:232h/1')
    p.add_argument('--audit-json', help='rtl backend: also write the compact 7-model audit table here')
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.backend == 'mpsse':
        parse_tck_list(args.tck_mhz_list)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    if args.backend == 'rtl':
        suites = {'rtl_audit': suite_rtl}
    else:
        table = {'models': suite_models, 'scenes': suite_scenes, 'resnet': suite_resnet}
        suites = table if args.suite == 'all' else {args.suite: table[args.suite]}
    results = {}
    for name, fn in suites.items():
        try:
            results[name] = fn(args)
        except Exception as exc:
            results[name] = dict(suite=name, passed=False, fpga_available=False, fpga_validated=False,
                                 errors=[f'{type(exc).__name__}: {exc}'])
        (args.out_dir / f'{name}.json').write_text(json.dumps(results[name], indent=2) + '\n', encoding='utf-8')
    passed = all(r['passed'] for r in results.values())
    summary = dict(started_utc=started, backend=args.backend, sys_clk_mhz=args.sys_clk_mhz,
                   tck_runs=[label for label, _, _ in transport_runs(args)] if args.backend != 'rtl' else [],
                   commit=_git_rev(), passed=passed,
                   fpga_available=any(r.get('fpga_available') for r in results.values()),
                   fpga_validated=bool(results) and all(r.get('fpga_validated') for r in results.values()),
                   suites={k: dict(passed=v['passed'], fpga_validated=v.get('fpga_validated', False),
                                   skipped=v.get('skipped', [])) for k, v in results.items()})
    (args.out_dir / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(f"{'PASS' if passed else 'FAIL'}: {args.out_dir / 'summary.json'}", flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
