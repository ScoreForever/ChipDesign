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
from .ir import requant_np, wrap32_np  # noqa: E402
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


class ReferenceBackend:
    """Bit-exact ABI 5.0 backend verifying nibble packing/unpacking on every tile."""

    def __init__(self) -> None:
        self.resident_key: tuple | None = None
        self.model_loads = 0
        self.tile_infers = 0
        self.jtag_model_bytes = 0
        self.jtag_input_bytes = 0
        self.jtag_output_bytes = 0
        self.npu_cycles = 0

    def load_program(self, group: ProgramGroup) -> None:
        key = _package_residency_key(group.package)
        if self.resident_key != key:
            self.resident_key = key
            self.model_loads += 1
            self.jtag_model_bytes += len(group.package.image)

    def infer_tile(self, group: ProgramGroup, input_hwc: np.ndarray) -> np.ndarray:
        m = group.package.manifest
        flat_in = np.asarray(input_hwc, dtype=np.int64).reshape(-1).tolist()
        encoded_in = abi.encode_tensor(flat_in, m['input_dtype'])
        if len(encoded_in) != m['input_bytes']:
            raise ValueError(f'Tile input bytes mismatch: {len(encoded_in)} vs {m["input_bytes"]}')
        decoded_in = abi.decode_tensor(encoded_in, m['input_dtype'], m['input_elements'])
        out_vals = abi.reference(group.sub_model, decoded_in)[group.sub_model['output']]
        encoded_out = abi.encode_tensor(out_vals, m['output_dtype'])
        if len(encoded_out) != m['output_bytes']:
            raise ValueError(f'Tile output bytes mismatch: {len(encoded_out)} vs {m["output_bytes"]}')
        decoded_out = abi.decode_tensor(encoded_out, m['output_dtype'], m['output_elements'])
        self.tile_infers += 1
        self.jtag_input_bytes += len(encoded_in)
        self.jtag_output_bytes += len(encoded_out)
        return np.asarray(decoded_out, dtype=np.int64).reshape(m['output_shape'])


class NpuBackend:
    """Real hardware / EGo1 FPGA backend over nocpu_jtag.Npu with weight-stationary caching."""

    def __init__(self, npu, *, read_cycles: bool = True, timeout: float = 30.0) -> None:
        self.npu = npu
        self.read_cycles = read_cycles
        self.timeout = timeout
        self.resident_key: tuple | None = None
        self.model_loads = 0
        self.tile_infers = 0
        self.jtag_model_bytes = 0
        self.jtag_input_bytes = 0
        self.jtag_output_bytes = 0
        self.npu_cycles = 0

    def load_program(self, group: ProgramGroup) -> None:
        key = _package_residency_key(group.package)
        if self.resident_key != key:
            self.npu.load_model(group.package)
            self.resident_key = key
            self.model_loads += 1
            self.jtag_model_bytes += len(group.package.image)

    def infer_tile(self, group: ProgramGroup, input_hwc: np.ndarray) -> np.ndarray:
        m = group.package.manifest
        flat_in = np.asarray(input_hwc, dtype=np.int64).reshape(-1).tolist()
        encoded_in = abi.encode_tensor(flat_in, m['input_dtype'])
        raw_out = self.npu.infer(encoded_in, timeout=self.timeout)
        decoded_out = abi.decode_tensor(raw_out, m['output_dtype'], m['output_elements'])
        self.tile_infers += 1
        self.jtag_input_bytes += len(encoded_in)
        self.jtag_output_bytes += len(raw_out)
        if self.read_cycles:
            self.npu_cycles += int(self.npu.read(0x38))
        return np.asarray(decoded_out, dtype=np.int64).reshape(m['output_shape'])


class RtlBackend:
    """Real Icarus Verilog (`iverilog` + `tb_npu_program.sv`) hardware RTL simulator backend."""

    def __init__(self, work_dir: str | Path | None = None, *, cols: int = 8) -> None:
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
        self.resident_key: tuple | None = None
        self.model_loads = 0
        self.tile_infers = 0
        self.jtag_model_bytes = 0
        self.jtag_input_bytes = 0
        self.jtag_output_bytes = 0
        self.npu_cycles = 0
        self._case_idx = 0

    def close(self) -> None:
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None

    def load_program(self, group: ProgramGroup) -> None:
        key = _package_residency_key(group.package)
        if self.resident_key != key:
            self.resident_key = key
            self.model_loads += 1
            self.jtag_model_bytes += len(group.package.image)

    def infer_tile(self, group: ProgramGroup, input_hwc: np.ndarray) -> np.ndarray:
        m = group.package.manifest
        flat_in = np.asarray(input_hwc, dtype=np.int64).reshape(-1).tolist()
        case = f't{self._case_idx:04d}_{group.program_id}'
        self._case_idx += 1
        # run_program_regression.simulate checks EVERY RTL activation write bit-for-bit against abi.reference!
        row = self._reg.simulate(
            self.work_dir, self.image, case, group.sub_model, flat_in, self._cols, True, False,
        )
        out_vals = abi.reference(group.sub_model, flat_in)[group.sub_model['output']]
        self.tile_infers += 1
        self.jtag_input_bytes += m['input_bytes']
        self.jtag_output_bytes += m['output_bytes']
        if 'cycles' in row and row['cycles']:
            self.npu_cycles += int(row['cycles'][0])
        return np.asarray(out_vals, dtype=np.int64).reshape(m['output_shape'])


def _extract_tile_input(src_arr: np.ndarray, call: TileCall) -> np.ndarray:
    """Extract padded HWC tile (or flattened FC channel slice) matching TileCall."""
    if call.flatten_input:
        flat = src_arr.reshape(-1)
        return flat[call.src_c0:call.src_c1].reshape(1, 1, call.src_c1 - call.src_c0)
    h, w, _ = src_arr.shape
    ih = call.src_y1 - call.src_y0
    iw = call.src_x1 - call.src_x0
    ic = call.src_c1 - call.src_c0
    tile = np.full((ih, iw, ic), call.pad_value, dtype=np.int64)
    sy0 = max(0, call.src_y0)
    sy1 = min(h, call.src_y1)
    sx0 = max(0, call.src_x0)
    sx1 = min(w, call.src_x1)
    if sy1 > sy0 and sx1 > sx0:
        dy0 = sy0 - call.src_y0
        dx0 = sx0 - call.src_x0
        tile[dy0:dy0 + (sy1 - sy0), dx0:dx0 + (sx1 - sx0), :] = src_arr[sy0:sy1, sx0:sx1, call.src_c0:call.src_c1]
    return tile


class TiledRuntime:
    """Execute a compiled TiledPlan on any backend (Reference, RTL, or live FPGA)."""

    def __init__(self, backend) -> None:
        self.backend = backend

    def run(self, plan: TiledPlan, input_hwc: np.ndarray) -> tuple[np.ndarray, dict]:
        t0 = time.perf_counter()
        host_ms = 0.0
        inp_arr = np.asarray(input_hwc, dtype=np.int64).reshape(plan.graph.input.shape)
        buffers: dict[str, np.ndarray] = {plan.graph.input.name: inp_arr}

        m0 = getattr(self.backend, 'model_loads', 0)
        ti0 = getattr(self.backend, 'tile_infers', 0)
        jm0 = getattr(self.backend, 'jtag_model_bytes', 0)
        ji0 = getattr(self.backend, 'jtag_input_bytes', 0)
        jo0 = getattr(self.backend, 'jtag_output_bytes', 0)
        cy0 = getattr(self.backend, 'npu_cycles', 0)

        for step in plan.steps:
            if isinstance(step, ProgramGroup):
                self.backend.load_program(step)
                for call in step.calls:
                    t_h0 = time.perf_counter()
                    tile_in = _extract_tile_input(buffers[call.src_name], call)
                    host_ms += (time.perf_counter() - t_h0) * 1000.0

                    tile_out = self.backend.infer_tile(step, tile_in)

                    t_h1 = time.perf_counter()
                    valid = tile_out[:call.valid_oh, :call.valid_ow, :]
                    if call.dst_name not in buffers:
                        td = plan.tensor_descs[call.dst_name]
                        buffers[call.dst_name] = np.zeros(td.shape, dtype=np.int64)
                    dst_buf = buffers[call.dst_name]
                    if call.accum_mode == 'add_i32':
                        cur = dst_buf[call.dst_y0:call.dst_y1, call.dst_x0:call.dst_x1, call.dst_c0:call.dst_c1]
                        dst_buf[call.dst_y0:call.dst_y1, call.dst_x0:call.dst_x1, call.dst_c0:call.dst_c1] = wrap32_np(cur + valid)
                    else:
                        dst_buf[call.dst_y0:call.dst_y1, call.dst_x0:call.dst_x1, call.dst_c0:call.dst_c1] = valid
                    host_ms += (time.perf_counter() - t_h1) * 1000.0
            elif isinstance(step, HostPostOp):
                t_h = time.perf_counter()
                a = step.attrs
                if step.op == 'Requant':
                    x = buffers[step.inputs[0]]
                    lo_q, hi_q = a.get('clamp', [0, 15] if step.out_dtype == 'u4' else [-8, 7])
                    buffers[step.output] = requant_np(x, a['multiplier'], a['shift'], int(lo_q), int(hi_q))
                elif step.op == 'ReLU':
                    buffers[step.output] = np.maximum(buffers[step.inputs[0]], 0)
                elif step.op == 'Add':
                    wa, wb = [int(v) for v in a.get('weights', [1, 1])]
                    buffers[step.output] = wrap32_np(wa * buffers[step.inputs[0]] + wb * buffers[step.inputs[1]])
                elif step.op == 'CPUFallback':
                    kind = a.get('operator', '')
                    if kind == 'Softmax':
                        axis = int(a.get('axis', -1))
                        in_sc = float(a.get('input_scale', 1.0))
                        xf = buffers[step.inputs[0]].astype(np.float64) * in_sc
                        xf = xf - xf.max(axis=axis, keepdims=True)
                        ex = np.exp(xf)
                        buffers[step.output] = (ex / ex.sum(axis=axis, keepdims=True)).astype(np.float32)
                    else:
                        raise NotImplementedError(f'Unsupported CPUFallback op: {kind!r}')
                else:
                    raise ValueError(f'Unknown HostPostOp: {step.op!r}')
                host_ms += (time.perf_counter() - t_h) * 1000.0

        wall_ms = (time.perf_counter() - t0) * 1000.0
        telemetry = {
            'model_loads': getattr(self.backend, 'model_loads', 0) - m0,
            'tile_infers': getattr(self.backend, 'tile_infers', 0) - ti0,
            'jtag_model_bytes': getattr(self.backend, 'jtag_model_bytes', 0) - jm0,
            'jtag_input_bytes': getattr(self.backend, 'jtag_input_bytes', 0) - ji0,
            'jtag_output_bytes': getattr(self.backend, 'jtag_output_bytes', 0) - jo0,
            'npu_cycles': getattr(self.backend, 'npu_cycles', 0) - cy0,
            'host_ms': host_ms,
            'wall_ms': wall_ms,
        }
        return buffers[plan.graph.output], telemetry
