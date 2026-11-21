"""Unified TiledRuntime supporting Reference, real NPU/FPGA, and Icarus Verilog RTL backends."""
from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
import sys
import tempfile
import time
import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
for p in (_ROOT / 'software/host', _ROOT / 'hardware/npu/scripts'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import npu_program as abi  # noqa: E402
from .ir import GraphIR, NodeIR, TensorDesc, oracle_reference, requant_np, wrap32_np  # noqa: E402
from .tiler import HostPostOp, ProgramGroup, TileCall, TiledPlan  # noqa: E402


def _package_residency_key(pkg: abi.Package) -> tuple:
    """Content + ABI configuration fingerprint so residency caching stays safe across distinct plans."""
    m = pkg.manifest
    return (
        hashlib.sha256(pkg.image).hexdigest(),
        int(m['model_bytes']),
        int(m['op_count']),
        int(m['input_base']),
        int(m['input_bytes']),
        int(m['output_base']),
        int(m['output_bytes']),
        str(m['input_dtype']),
    )


# ---------------------------------------------------------------------------
# Vectorized ABI 5.0 tensor codecs (bit-identical to npu_program.encode/decode_tensor)
# ---------------------------------------------------------------------------
_NIBBLE_RANGE = {'i4': (-8, 7), 'u4': (0, 15)}


def pack_i4_np(values, dtype: str = 'i4') -> bytes:
    """Pack i4/u4 values low nibble first; an odd tail gets a zero high nibble."""
    v = np.asarray(values, dtype=np.int64).reshape(-1)
    lo, hi = _NIBBLE_RANGE[dtype]
    if v.size and (v.min() < lo or v.max() > hi):
        raise ValueError(f'value outside {dtype} range')
    nib = (v & 15).astype(np.uint8)
    if nib.size % 2:
        nib = np.append(nib, np.uint8(0))
    pairs = nib.reshape(-1, 2)
    return (pairs[:, 0] | (pairs[:, 1] << 4)).astype(np.uint8).tobytes()


def unpack_i4_np(payload: bytes, count: int, dtype: str = 'i4') -> np.ndarray:
    if count < 0 or len(payload) != (count + 1) // 2:
        raise ValueError('packed INT4 length')
    raw = np.frombuffer(bytes(payload), dtype=np.uint8)
    if count % 2 and raw[-1] & 0xf0:
        raise ValueError('nonzero INT4 padding nibble')
    nib = np.stack([raw & 15, raw >> 4], axis=1).reshape(-1)[:count].astype(np.int64)
    return nib if dtype == 'u4' else (nib ^ 8) - 8


def encode_tensor_np(values, dtype: str) -> bytes:
    if dtype in _NIBBLE_RANGE:
        return pack_i4_np(values, dtype)
    if dtype == 'i32':
        v = np.asarray(values, dtype=np.int64).reshape(-1)
        if v.size and (v.min() < -(1 << 31) or v.max() >= 1 << 31):
            raise ValueError('value outside i32')
        return v.astype('<i4').tobytes()
    raise ValueError('tensor dtype must be i4, u4 or i32')


def decode_tensor_np(payload: bytes, dtype: str, count: int) -> np.ndarray:
    if dtype in _NIBBLE_RANGE:
        return unpack_i4_np(payload, count, dtype)
    if dtype == 'i32' and len(payload) == 4 * count:
        return np.frombuffer(bytes(payload), dtype='<i4').astype(np.int64)
    raise ValueError('invalid tensor dtype/length')


def encode_tile_inputs(group: ProgramGroup, tiles: list[np.ndarray]) -> bytes:
    """Host payload for one tile: in1, or ``in1 || zero pad to 4 B || in2`` for a two-input Add."""
    sm = group.sub_model
    payload = encode_tensor_np(tiles[0], sm['input']['dtype'])
    if len(tiles) > 1:
        payload += bytes(-len(payload) % 4) + encode_tensor_np(tiles[1], sm['input2']['dtype'])
    if len(payload) != group.package.manifest['input_bytes']:
        raise ValueError(f'Tile input bytes mismatch: {len(payload)} vs {group.package.manifest["input_bytes"]}')
    return payload


def decode_tile_inputs(group: ProgramGroup, payload: bytes) -> list[np.ndarray]:
    sm = group.sub_model
    specs = [sm['input']] + ([sm['input2']] if 'input2' in sm else [])
    out, at = [], 0
    for spec in specs:
        at = abi.align(at)
        count = int(np.prod(spec['shape']))
        size = 4 * count if spec['dtype'] == 'i32' else (count + 1) // 2
        out.append(decode_tensor_np(payload[at:at + size], spec['dtype'], count).reshape(spec['shape']))
        at += size
    return out


_SUB_GRAPH_CACHE: dict[int, tuple[dict, GraphIR, dict]] = {}


def _sub_graph(sub_model: dict) -> tuple[GraphIR, dict]:
    """GraphIR (with numpy weights) for a ProgramGroup sub-model, cached per sub-model object."""
    hit = _SUB_GRAPH_CACHE.get(id(sub_model))
    if hit is not None and hit[0] is sub_model:
        return hit[1], hit[2]
    nodes = []
    for row in sub_model['nodes']:
        attrs = {k: v for k, v in row.items() if k not in ('op', 'input', 'input2', 'output')}
        for key in ('weights', 'bias'):
            if key in attrs and row['op'] in ('Conv', 'FC'):
                attrs[key] = np.asarray(attrs[key], dtype=np.int64)
        inputs = [row['input']] + ([row['input2']] if row['op'] == 'Add' else [])
        nodes.append(NodeIR(row['op'], inputs, row['output'], attrs=attrs))
    spec = sub_model['input']
    graph = GraphIR(TensorDesc(spec['name'], tuple(spec['shape']), spec['dtype']), nodes, sub_model['output'])
    extra = {}
    if 'input2' in sub_model:
        s2 = sub_model['input2']
        extra[s2['name']] = TensorDesc(s2['name'], tuple(s2['shape']), s2['dtype'])
    _SUB_GRAPH_CACHE[id(sub_model)] = (sub_model, graph, extra)
    return graph, extra


def execute_sub_model(sub_model: dict, tiles: list[np.ndarray]) -> dict[str, np.ndarray]:
    """Exact ABI integer semantics of one tile program; returns every logical tensor."""
    graph, extra = _sub_graph(sub_model)
    extra_vals = {name: (desc, tiles[1]) for name, desc in extra.items()}
    return oracle_reference(graph, tiles[0], extra_vals)


class _Counters:
    def __init__(self) -> None:
        self.resident_key: tuple | None = None
        self.model_loads = 0
        self.tile_infers = 0
        self.jtag_model_bytes = 0
        self.jtag_input_bytes = 0
        self.jtag_output_bytes = 0
        self.npu_cycles = 0

    def _needs_load(self, group: ProgramGroup) -> bool:
        key = _package_residency_key(group.package)
        if self.resident_key == key:
            return False
        self.resident_key = key
        self.model_loads += 1
        self.jtag_model_bytes += len(group.package.image)
        return True

    def load_program(self, group: ProgramGroup) -> None:
        self._needs_load(group)

    def _count(self, n_in: int, n_out: int) -> None:
        self.tile_infers += 1
        self.jtag_input_bytes += n_in
        self.jtag_output_bytes += n_out


class ReferenceBackend(_Counters):
    """Bit-exact ABI 5.0 backend; every tile round-trips through the packed host wire format."""

    def infer_tile(self, group: ProgramGroup, *tiles: np.ndarray) -> np.ndarray:
        m = group.package.manifest
        payload = encode_tile_inputs(group, list(tiles))
        values = execute_sub_model(group.sub_model, decode_tile_inputs(group, payload))[group.sub_model['output']]
        encoded_out = encode_tensor_np(values, m['output_dtype'])
        if len(encoded_out) != m['output_bytes']:
            raise ValueError(f'Tile output bytes mismatch: {len(encoded_out)} vs {m["output_bytes"]}')
        self._count(len(payload), len(encoded_out))
        return decode_tensor_np(encoded_out, m['output_dtype'], m['output_elements']).reshape(m['output_shape'])


class NpuBackend(_Counters):
    """Real hardware / EGo1 FPGA backend over nocpu_jtag.Npu with weight-stationary caching."""

    def __init__(self, npu, *, read_cycles: bool = True, timeout: float = 30.0) -> None:
        super().__init__()
        self.npu = npu
        self.read_cycles = read_cycles
        self.timeout = timeout

    def load_program(self, group: ProgramGroup) -> None:
        key = _package_residency_key(group.package)
        if self.resident_key != key:
            self.resident_key = None  # a failed load leaves nothing resident
            self.npu.load_model(group.package)
            self._needs_load(group)

    def infer_tile(self, group: ProgramGroup, *tiles: np.ndarray) -> np.ndarray:
        m = group.package.manifest
        payload = encode_tile_inputs(group, list(tiles))
        try:
            raw_out = self.npu.infer(payload, timeout=self.timeout)
        except Exception:
            self.resident_key = None
            raise
        self._count(len(payload), len(raw_out))
        if self.read_cycles:
            self.npu_cycles += int(self.npu.read(0x38))
        return decode_tensor_np(raw_out, m['output_dtype'], m['output_elements']).reshape(m['output_shape'])


class _FullSramRecorder:
    """Host command recorder supporting the full 16 KB shared SRAM (`0x20000..0x24000`)."""

    def __init__(self, pkg, output: bytes) -> None:
        self.rows: list[str] = []
        self.regs = {
            0: abi.ABI, 0x50: 16384, 0x54: 8192,
            0x58: (8 << 16) | pkg.manifest['cols'], 0x5c: 1024, 0x60: 128, 0x6c: 0x0f200404,
        }
        self.output = output
        self.started = False
        self.pkg = pkg
        self.burst_addr = 0

    def exchange(self, op, addr=0, data=0):
        from nocpu_jtag import Op
        value = 0
        mask = 0xffffffff
        if op == Op.WRITE and addr < 0x10000:
            self.regs[addr] = data
        if op == Op.START:
            self.started = True
            value = 1
            mask = 0
        if op == Op.READ:
            if addr == 4:
                if self.started:
                    self.rows.append('f0 0 0 0 0 0')
                    value = 10
                    mask = 0x49
                else:
                    value = 2
                    mask = 0
            elif 0x20000 <= addr < 0x24000:
                at = addr - 0x20000 - self.pkg.manifest['output_base']
                chunk = self.output[at:at + 4]
                value = int.from_bytes(chunk.ljust(4, b'\0'), 'little')
                mask = (1 << (8 * len(chunk))) - 1
            else:
                value = self.regs.get(addr, 0)
        if op == Op.ACK:
            self.started = False
        if op in (Op.READ, Op.WRITE):
            self.burst_addr = addr + 4
        self.rows.append(f'{int(op):02x} {addr:08x} {int(data) & 0xffffffff:08x} 00 {mask:08x} {value:08x}')
        return value

    def burst_write(self, words):
        for w in words:
            self.rows.append(f'80 {self.burst_addr:08x} {w & 0xffffffff:08x} 00 00000000 00000000')
            self.burst_addr += 4

    def burst_read(self, count):
        values = []
        for _ in range(count):
            at = self.burst_addr - 0x20000 - self.pkg.manifest['output_base']
            chunk = self.output[at:at + 4]
            value = int.from_bytes(chunk.ljust(4, b'\0'), 'little')
            mask = (1 << (8 * len(chunk))) - 1
            self.rows.append(f'81 {self.burst_addr:08x} 0 00 {mask:08x} {value:08x}')
            self.burst_addr += 4
            values.append(value)
        return values


class RtlBackend(_Counters):
    """Real Icarus Verilog (`iverilog` + `tb_npu_program.sv`) hardware RTL simulator backend."""

    def __init__(self, work_dir: str | Path | None = None, *, cols: int = 8, keep_cases: bool = False) -> None:
        super().__init__()
        self.keep_cases = keep_cases
        self.last_cycles: int | None = None
        if not shutil.which('iverilog') or not shutil.which('vvp'):
            raise RuntimeError('iverilog/vvp required for RtlBackend')
        import run_program_regression as reg
        self._reg = reg
        self._cols = cols
        self._temp = None
        if work_dir is None:
            build_root = _ROOT / 'build'
            build_root.mkdir(parents=True, exist_ok=True)
            self._temp = tempfile.TemporaryDirectory(prefix='npu_compiler_rtl_', dir=build_root)
            self.work_dir = Path(self._temp.name)
        else:
            self.work_dir = Path(work_dir).resolve()
            self.work_dir.mkdir(parents=True, exist_ok=True)
        sources = (_ROOT / 'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
        self.image = self.work_dir / f'program_{cols}.vvp'
        if not self.image.is_file():
            self._reg.run(
                ['iverilog', '-g2012', '-s', 'tb_npu_program', '-o', self.image,
                 *sources, 'hardware/soc/sim/tb/tb_npu_program.sv'],
                self.work_dir / 'compile.log',
            )
        self._case_idx = 0

    def close(self) -> None:
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None

    def infer_tile(self, group: ProgramGroup, *tiles: np.ndarray) -> np.ndarray:
        import re
        from nocpu_jtag import Npu

        pkg = group.package
        m = pkg.manifest
        case_dir = self.work_dir / f't{self._case_idx:04d}_{group.program_id}'
        self._case_idx += 1
        case_dir.mkdir(parents=True, exist_ok=True)

        in_bytes = encode_tile_inputs(group, list(tiles))
        logical = execute_sub_model(group.sub_model, decode_tile_inputs(group, in_bytes))
        expected_vals = logical[group.sub_model['output']]
        expected_bytes = encode_tensor_np(expected_vals, m['output_dtype'])

        rec = _FullSramRecorder(pkg, expected_bytes)
        host = Npu(rec)
        host.load_model(pkg)
        if host.infer(in_bytes) != expected_bytes:
            raise AssertionError(f'RTL recorder mismatch on {group.program_id}')
        rec.rows.extend(['01 00000038 0 0 0 0', '01 00000040 0 0 0 0'])
        trace = case_dir / 'commands.txt'
        trace.write_text('\n'.join(rec.rows) + '\n', encoding='utf-8')

        # Golden per-write activation SRAM events, in the core's (channel group, position, lane) order.
        n_inputs = len(m.get('input_tensors', [None]))
        events = [_write_events(logical[name], info, int(m['cols']))
                  for name, info in list(m['tensors'].items())[n_inputs:]]
        events = np.concatenate(events) if events else np.zeros(0, dtype=np.uint64)
        event_file = case_dir / 'writes.hex'
        event_file.write_text(''.join(f'{int(v):016x}\n' for v in events), encoding='utf-8')
        log = self._reg.run(
            ['vvp', self.image,
             f'+TRACE={trace.relative_to(_ROOT).as_posix()}',
             f'+EVENTS={event_file.relative_to(_ROOT).as_posix()}',
             f'+EVENT_COUNT={len(events)}'],
            case_dir / 'rtl.log',
        )
        if 'PASS program' not in log:
            raise RuntimeError(f'RTL simulation failed on {group.program_id}:\n{log[-2000:]}')
        cycles = [int(v) for a, v in re.findall(r'METRIC reg=([0-9a-f]+) value=(\d+)', log) if int(a, 16) == 0x38]
        self._count(m['input_bytes'], m['output_bytes'])
        if cycles:
            self.last_cycles = cycles[0]
            self.npu_cycles += cycles[0]
        if not self.keep_cases:
            shutil.rmtree(case_dir, ignore_errors=True)
        return np.asarray(expected_vals, dtype=np.int64).reshape(m['output_shape'])


def _write_events(values: np.ndarray, info: dict, cols: int) -> np.ndarray:
    """Expected SRAM write events (addr<<48 | mask<<32 | data) for one physical tensor."""
    flat = np.asarray(values, dtype=np.int64).reshape(-1)
    channels = int(info['shape'][2])
    positions = flat.size // channels
    base = int(info['base']) * 2  # nibble address
    wide = info['dtype'] == 'i32'
    rows = []
    for oc in range(0, channels, cols):
        width = min(cols, channels - oc)
        lanes = np.arange(0, width, 1 if wide else 2)
        index = (np.arange(positions)[:, None] * channels + oc + lanes[None, :]).reshape(-1)
        if wide:
            addr = base + index * 8
            mask = np.full(index.shape, 255, dtype=np.int64)
            data = flat[index] & 0xffffffff
        else:
            pair = np.broadcast_to(lanes + 1 < width, (positions, lanes.size)).reshape(-1)
            nxt = np.where(pair, flat[np.minimum(index + 1, flat.size - 1)] & 15, 0)
            data = (flat[index] & 15) | (nxt << 4)
            mask = np.where(pair, 3, 1)
            if flat.size % 2:
                last = index + pair.astype(np.int64) == flat.size - 1
                mask = np.where(last, (mask << 1) | 1, mask)
            addr = base + index
        rows.append((addr.astype(np.uint64) << np.uint64(48)) | (mask.astype(np.uint64) << np.uint64(32))
                    | data.astype(np.uint64))
    return np.concatenate(rows) if rows else np.zeros(0, dtype=np.uint64)


def _extract_window(src_arr: np.ndarray, call: TileCall, pad_value: int) -> np.ndarray:
    """Padded HWC window [src_y0:src_y1, src_x0:src_x1, src_c0:src_c1] (or flattened FC slice)."""
    if call.flatten_input:
        return src_arr.reshape(-1)[call.src_c0:call.src_c1].reshape(1, 1, call.src_c1 - call.src_c0)
    h, w, _ = src_arr.shape
    sy0, sy1 = max(0, call.src_y0), min(h, call.src_y1)
    sx0, sx1 = max(0, call.src_x0), min(w, call.src_x1)
    inner = src_arr[sy0:sy1, sx0:sx1, call.src_c0:call.src_c1]
    pads = ((sy0 - call.src_y0, call.src_y1 - sy1), (sx0 - call.src_x0, call.src_x1 - sx1), (0, 0))
    if not any(p for pair in pads for p in pair):
        return inner
    if sy1 <= sy0 or sx1 <= sx0:
        return np.full((call.src_y1 - call.src_y0, call.src_x1 - call.src_x0, call.src_c1 - call.src_c0),
                       pad_value, dtype=np.int64)
    return np.pad(inner, pads, constant_values=pad_value)


def _extract_tile_input(src_arr: np.ndarray, call: TileCall) -> np.ndarray:
    return _extract_window(src_arr, call, call.pad_value)


def _run_host_op(step: HostPostOp, buffers: dict[str, np.ndarray]) -> np.ndarray:
    a = step.attrs
    x = buffers[step.inputs[0]]
    if step.op == 'Requant':
        lo_q, hi_q = a.get('clamp', [0, 15] if step.out_dtype == 'u4' else [-8, 7])
        return requant_np(x, a['multiplier'], a['shift'], int(lo_q), int(hi_q))
    if step.op == 'ReLU':
        return np.maximum(x, 0)
    if step.op == 'Add':
        wa, wb = [int(v) for v in a.get('weights', [1, 1])]
        return wrap32_np(wa * x + wb * buffers[step.inputs[1]])
    if step.op == 'CPUFallback':
        if a.get('operator', '') != 'Softmax':
            raise NotImplementedError(f'Unsupported CPUFallback op: {a.get("operator")!r}')
        axis = int(a.get('axis', -1))
        xf = x.astype(np.float64) * float(a.get('input_scale', 1.0))
        ex = np.exp(xf - xf.max(axis=axis, keepdims=True))
        return (ex / ex.sum(axis=axis, keepdims=True)).astype(np.float32)
    raise ValueError(f'Unknown HostPostOp: {step.op!r}')


_TELEMETRY = ('model_loads', 'tile_infers', 'jtag_model_bytes', 'jtag_input_bytes', 'jtag_output_bytes', 'npu_cycles')


class TiledRuntime:
    """Execute a compiled TiledPlan on any backend (Reference, RTL, or live FPGA)."""

    def __init__(self, backend) -> None:
        self.backend = backend

    def _snapshot(self) -> dict:
        return {k: getattr(self.backend, k, 0) for k in _TELEMETRY}

    def _run_step(self, plan: TiledPlan, step, buffers: dict[str, np.ndarray]) -> float:
        """Execute one plan step on one frame's buffers; returns host milliseconds."""
        t0 = time.perf_counter()
        if isinstance(step, HostPostOp):
            buffers[step.output] = _run_host_op(step, buffers)
            return (time.perf_counter() - t0) * 1000.0
        host_ms = 0.0
        self.backend.load_program(step)
        for call in step.calls:
            t_h0 = time.perf_counter()
            tiles = [_extract_tile_input(buffers[call.src_name], call)]
            if call.src2_name is not None:
                tiles.append(_extract_window(buffers[call.src2_name], call, call.src2_pad_value))
            host_ms += (time.perf_counter() - t_h0) * 1000.0
            tile_out = self.backend.infer_tile(step, *tiles)
            t_h1 = time.perf_counter()
            valid = tile_out[:call.valid_oh, :call.valid_ow, :]
            dst_buf = buffers.get(call.dst_name)
            if dst_buf is None:
                dst_buf = buffers[call.dst_name] = np.zeros(plan.tensor_descs[call.dst_name].shape, dtype=np.int64)
            sl = (slice(call.dst_y0, call.dst_y1), slice(call.dst_x0, call.dst_x1), slice(call.dst_c0, call.dst_c1))
            dst_buf[sl] = wrap32_np(dst_buf[sl] + valid) if call.accum_mode == 'add_i32' else valid
            host_ms += (time.perf_counter() - t_h1) * 1000.0
        return host_ms

    def run(self, plan: TiledPlan, input_hwc: np.ndarray) -> tuple[np.ndarray, dict]:
        t0 = time.perf_counter()
        before = self._snapshot()
        buffers = {plan.graph.input.name: np.asarray(input_hwc, dtype=np.int64).reshape(plan.graph.input.shape)}
        host_ms = sum(self._run_step(plan, step, buffers) for step in plan.steps)
        after = self._snapshot()
        telemetry = {k: after[k] - before[k] for k in _TELEMETRY}
        telemetry.update(host_ms=host_ms, wall_ms=(time.perf_counter() - t0) * 1000.0)
        return buffers[plan.graph.output], telemetry

    def run_batch(self, plan: TiledPlan, frames) -> list[tuple[np.ndarray, dict]]:
        """Steps outer, frames inner: each resident program is loaded once for the whole batch."""
        frames = list(frames)
        if not frames:
            return []
        buffers = [{plan.graph.input.name: np.asarray(f, dtype=np.int64).reshape(plan.graph.input.shape)}
                   for f in frames]
        totals = [dict.fromkeys(_TELEMETRY + ('host_ms', 'wall_ms'), 0) for _ in frames]
        for step in plan.steps:
            for state, total in zip(buffers, totals):
                t0 = time.perf_counter()
                before = self._snapshot()
                total['host_ms'] += self._run_step(plan, step, state)
                after = self._snapshot()
                for k in _TELEMETRY:
                    total[k] += after[k] - before[k]
                total['wall_ms'] += (time.perf_counter() - t0) * 1000.0
        return [(state[plan.graph.output], total) for state, total in zip(buffers, totals)]


TiledPlanRunner = TiledRuntime
