"""Canonical hardware-independent quantized DAG IR and fast exact integer oracle."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import sys
import numpy as np

_HOST_DIR = str(Path(__file__).resolve().parents[1] / 'host')
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)

import npu_program as abi  # noqa: E402

VALID_DTYPES = ('i4', 'u4', 'i32', 'f32')
NPU_OPS = ('Conv', 'FC', 'MaxPool', 'AvgPool', 'Add', 'ReLU', 'Requant')


@dataclass
class TensorDesc:
    name: str
    shape: tuple[int, int, int]
    dtype: str = 'i4'
    scale: float = 1.0

    def __post_init__(self) -> None:
        self.shape = tuple(int(v) for v in self.shape)
        if len(self.shape) != 3 or any(v <= 0 for v in self.shape):
            raise ValueError(f'TensorDesc {self.name!r} requires positive 3D HWC shape, got {self.shape}')
        if self.dtype not in VALID_DTYPES:
            raise ValueError(f'Unsupported dtype {self.dtype!r} for tensor {self.name!r}')


@dataclass
class NodeIR:
    op: str
    inputs: list[str]
    output: str
    attrs: dict = field(default_factory=dict)
    backend: str = 'npu'
    name: str = ''

    def __post_init__(self) -> None:
        self.inputs = list(self.inputs)
        if not self.name:
            self.name = self.output


@dataclass
class GraphIR:
    input: TensorDesc
    nodes: list[NodeIR]
    output: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'input': asdict(self.input),
            'nodes': [asdict(n) for n in self.nodes],
            'output': self.output,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'GraphIR':
        inp = TensorDesc(**data['input'])
        nodes = [NodeIR(**n) for n in data['nodes']]
        return cls(input=inp, nodes=nodes, output=data['output'], metadata=dict(data.get('metadata', {})))


def wrap32_np(arr: np.ndarray) -> np.ndarray:
    """Wrap int64 array to signed 32-bit two's complement range (matches npu_program.wrap32)."""
    return ((arr.astype(np.int64) + (1 << 31)) % (1 << 32) - (1 << 31)).astype(np.int64)


def q8_np(multiplier, shift) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized npu_program.q8: round Q15 to 8 bits; a carry to 256 renormalizes (or saturates at shift 31)."""
    mult = np.asarray(multiplier, dtype=np.int64)
    sh = np.asarray(shift, dtype=np.int64)
    m = (mult + 64) >> 7
    carry = m == 256
    m = np.where(carry, np.where(sh < 31, 128, 255), m)
    sh = np.where(carry & (sh < 31), sh + 1, sh)
    return m.astype(np.int64), sh.astype(np.int64)


def requant_np(x: np.ndarray, multiplier, shift, lo: int = -8, hi: int = 7) -> np.ndarray:
    """Vectorized bit-exact equivalent of npu_program.requant for HWC int64 tensors."""
    c = x.shape[-1]
    mult_arr = np.asarray(multiplier, dtype=np.int64).reshape(-1)
    shift_arr = np.asarray(shift, dtype=np.int64).reshape(-1)
    if mult_arr.size == 1:
        mult_arr = np.broadcast_to(mult_arr, (c,))
    if shift_arr.size == 1:
        shift_arr = np.broadcast_to(shift_arr, (c,))
    if mult_arr.size != c or shift_arr.size != c:
        raise ValueError(f'Requant channel mismatch: x has {c}, mult={mult_arr.size}, shift={shift_arr.size}')

    if (mult_arr < 0).any() or (mult_arr >= 1 << 15).any() or (shift_arr < -31).any() or (shift_arr > 31).any():
        raise ValueError('invalid INT4/u4 Q15 parameters')
    m_q8, s_eff = q8_np(mult_arr, shift_arr)

    z = wrap32_np(x)
    pos_shift = np.maximum(s_eff, 0)
    # Apply saturating left shift for shift > 0
    z_shifted = np.clip(z * (np.int64(1) << pos_shift), -(1 << 31), (1 << 31) - 1)
    rshift = 8 + np.maximum(-s_eff, 0)
    half = np.int64(1) << (rshift - 1)
    y = (z_shifted * m_q8 + half) >> rshift
    return np.clip(y, lo, hi).astype(np.int64)


def pad_value_for_dtype(dtype: str, op: str) -> int:
    """Padding fill value matching hardware / ONNX semantics."""
    if op == 'MaxPool':
        if dtype == 'u4':
            return 0
        if dtype == 'i4':
            return -8
        return -(1 << 31)
    return 0


def infer_tensors(graph: GraphIR, extra_inputs: dict[str, TensorDesc] | None = None) -> dict[str, TensorDesc]:
    """Infer HWC shape and dtype for every tensor in GraphIR (plus optional extra graph inputs)."""
    tensors: dict[str, TensorDesc] = {graph.input.name: graph.input, **(extra_inputs or {})}
    for n in graph.nodes:
        t = tensors[n.inputs[0]]
        h, w, c = t.shape
        a = n.attrs
        op = n.op
        if op == 'Conv':
            oc = int(a['channels'])
            kh, kw = abi.pair(a.get('kernel', 3), 'kernel')
            sh, sw = abi.pair(a.get('stride', 1), 'stride')
            pt, pl, pb, pr = [int(v) for v in a.get('pads', [0, 0, 0, 0])]
            oh = (h + pt + pb - kh) // sh + 1
            ow = (w + pl + pr - kw) // sw + 1
            tensors[n.output] = TensorDesc(n.output, (oh, ow, oc), 'i32')
        elif op == 'FC':
            oc = int(a['channels'])
            tensors[n.output] = TensorDesc(n.output, (1, 1, oc), 'i32')
        elif op in ('MaxPool', 'AvgPool'):
            kh, kw = abi.pair(a.get('kernel', 2), 'kernel')
            sh, sw = abi.pair(a.get('stride', [kh, kw]), 'stride')
            pt, pl, pb, pr = [int(v) for v in a.get('pads', [0, 0, 0, 0])]
            oh = (h + pt + pb - kh) // sh + 1
            ow = (w + pl + pr - kw) // sw + 1
            out_dtype = 'i32' if op == 'AvgPool' else t.dtype
            tensors[n.output] = TensorDesc(n.output, (oh, ow, c), out_dtype)
        elif op == 'Add':
            t2 = tensors[n.inputs[1]]
            if t2.shape != t.shape:
                raise ValueError(f'Add shape mismatch: {t.shape} vs {t2.shape} at {n.output}')
            tensors[n.output] = TensorDesc(n.output, t.shape, 'i32')
        elif op == 'ReLU':
            tensors[n.output] = TensorDesc(n.output, t.shape, t.dtype)
        elif op == 'Requant':
            out_dtype = a.get('dtype', 'i4')
            tensors[n.output] = TensorDesc(n.output, t.shape, out_dtype)
        elif op == 'CPUFallback':
            out_dtype = 'f32' if a.get('operator') == 'Softmax' else t.dtype
            tensors[n.output] = TensorDesc(n.output, t.shape, out_dtype)
        else:
            raise ValueError(f'Unknown IR op: {op!r}')
    return tensors


def _conv2d_exact(x_hwc: np.ndarray, weights_flat, bias, kh: int, kw: int, sh: int, sw: int,
                  pads: list[int], oc: int, groups: int) -> np.ndarray:
    """Fast bit-exact integer 2D convolution using float64 BLAS GEMM (exact for |val| < 2^53)."""
    h, w, c = x_hwc.shape
    pt, pl, pb, pr = pads
    oh = (h + pt + pb - kh) // sh + 1
    ow = (w + pl + pr - kw) // sw + 1
    xp = np.pad(x_hwc.astype(np.int64), ((pt, pb), (pl, pr), (0, 0)), constant_values=0)
    # sliding_window_view on axes (0, 1) yields shape (H_win, W_win, C, kh, kw)
    win = np.lib.stride_tricks.sliding_window_view(xp, (kh, kw), axis=(0, 1))[::sh, ::sw]
    bias_arr = np.asarray(bias, dtype=np.int64).reshape(1, 1, oc)
    if groups != 1:
        if groups != c or oc != c:
            raise ValueError(f'Only depthwise grouping supported, got groups={groups}, ic={c}, oc={oc}')
        wt = np.asarray(weights_flat, dtype=np.int64).reshape(oc, kh, kw)
        # win: (oh, ow, c, kh, kw) * wt: (c, kh, kw) -> sum over (kh, kw)
        out = np.einsum('hwcij,cij->hwc', win.astype(np.float64), wt.astype(np.float64), optimize=True)
        return wrap32_np(np.rint(out).astype(np.int64) + bias_arr)
    # Standard conv: reorder patches to (oh*ow, kh*kw*c) matching weight layout (oc, kh, kw, c)
    cols = np.ascontiguousarray(win.transpose(0, 1, 3, 4, 2)).reshape(oh * ow, kh * kw * c).astype(np.float64)
    wt = np.asarray(weights_flat, dtype=np.float64).reshape(oc, kh * kw * c)
    prod = cols @ wt.T  # exact in float64 because max magnitude << 2^53
    out = np.rint(prod).astype(np.int64).reshape(oh, ow, oc) + bias_arr
    return wrap32_np(out)


_DTYPE_RANGE = {'i32': (-(1 << 31), (1 << 31) - 1), 'i4': (-8, 7), 'u4': (0, 15)}


def oracle_reference(graph: GraphIR, input_hwc: np.ndarray,
                     extra_inputs: dict[str, tuple[TensorDesc, np.ndarray]] | None = None) -> dict[str, np.ndarray]:
    """Execute full GraphIR with exact integer semantics and NO hardware capacity limits.

    extra_inputs maps additional input tensor names to (TensorDesc, value); used by
    two-input hardware tiles such as a tiled residual Add.
    """
    extra = extra_inputs or {}
    tensors = infer_tensors(graph, {k: d for k, (d, _) in extra.items()})
    values: dict[str, np.ndarray] = {}
    for desc, arr in [(graph.input, input_hwc)] + list(extra.values()):
        arr = np.asarray(arr, dtype=np.int64).reshape(desc.shape)
        lo, hi = _DTYPE_RANGE[desc.dtype]
        if arr.min() < lo or arr.max() > hi:
            raise ValueError(f'Input values outside {desc.dtype} range [{lo}, {hi}]')
        values[desc.name] = arr
    for n in graph.nodes:
        op = n.op
        a = n.attrs
        x = values[n.inputs[0]]
        t_in = tensors[n.inputs[0]]
        if op == 'Conv':
            kh, kw = abi.pair(a.get('kernel', 3), 'kernel')
            sh, sw = abi.pair(a.get('stride', 1), 'stride')
            pads = [int(v) for v in a.get('pads', [0, 0, 0, 0])]
            oc = int(a['channels'])
            groups = int(a.get('groups', 1))
            bias = a.get('bias', [0] * oc)
            values[n.output] = _conv2d_exact(x, a['weights'], bias, kh, kw, sh, sw, pads, oc, groups)
        elif op == 'FC':
            oc = int(a['channels'])
            x_flat = x.reshape(1, -1).astype(np.float64)
            wt = np.asarray(a['weights'], dtype=np.float64).reshape(oc, x_flat.shape[1])
            bias = np.asarray(a.get('bias', [0] * oc), dtype=np.int64).reshape(1, 1, oc)
            prod = np.rint(x_flat @ wt.T).astype(np.int64).reshape(1, 1, oc) + bias
            values[n.output] = wrap32_np(prod)
        elif op in ('MaxPool', 'AvgPool'):
            kh, kw = abi.pair(a.get('kernel', 2), 'kernel')
            sh, sw = abi.pair(a.get('stride', [kh, kw]), 'stride')
            pt, pl, pb, pr = [int(v) for v in a.get('pads', [0, 0, 0, 0])]
            fill = pad_value_for_dtype(t_in.dtype, op)
            xp = np.pad(x.astype(np.int64), ((pt, pb), (pl, pr), (0, 0)), constant_values=fill)
            win = np.lib.stride_tricks.sliding_window_view(xp, (kh, kw), axis=(0, 1))[::sh, ::sw]
            if op == 'MaxPool':
                values[n.output] = win.max(axis=(-1, -2)).astype(np.int64)
            else:
                values[n.output] = wrap32_np(win.sum(axis=(-1, -2)))
        elif op == 'Add':
            wa, wb = [int(v) for v in a.get('weights', [1, 1])]
            x2 = values[n.inputs[1]].astype(np.int64)
            values[n.output] = wrap32_np(wa * x.astype(np.int64) + wb * x2)
        elif op == 'ReLU':
            values[n.output] = np.maximum(x.astype(np.int64), 0)
        elif op == 'Requant':
            out_dtype = a.get('dtype', 'i4')
            lo_q, hi_q = a.get('clamp', [0, 15] if out_dtype == 'u4' else [-8, 7])
            values[n.output] = requant_np(x, a['multiplier'], a['shift'], int(lo_q), int(hi_q))
        elif op == 'CPUFallback':
            kind = a.get('operator', '')
            if kind == 'Softmax':
                axis = int(a.get('axis', -1))
                in_sc = float(a.get('input_scale', 1.0))
                xf = x.astype(np.float64) * in_sc
                xf = xf - xf.max(axis=axis, keepdims=True)
                ex = np.exp(xf)
                values[n.output] = (ex / ex.sum(axis=axis, keepdims=True)).astype(np.float32)
            else:
                raise NotImplementedError(f'Unsupported CPUFallback operator: {kind!r}')
        else:
            raise ValueError(f'Unknown IR op: {op!r}')
    return values
