"""Weight-Stationary 3D Tiling + Small-Layer Subgraph Fusion Planner for ABI 5.0."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
import math
from pathlib import Path
import struct
import sys
import numpy as np

_HOST_DIR = str(Path(__file__).resolve().parents[1] / 'host')
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)

import npu_program as abi  # noqa: E402
from .ir import GraphIR, NodeIR, TensorDesc, infer_tensors, pad_value_for_dtype  # noqa: E402

MAX_K = 1024
MAX_OC = 256
MAX_SPATIAL = 255


@dataclass
class TileCall:
    """One NPU invocation for a spatial slice under a resident ProgramGroup."""
    src_name: str
    dst_name: str
    # Desired source spatial window in full-tensor coordinates (may extend outside [0, H)x[0, W))
    src_y0: int
    src_y1: int
    src_x0: int
    src_x1: int
    src_c0: int
    src_c1: int
    # Destination slice in full output tensor
    dst_y0: int
    dst_y1: int
    dst_x0: int
    dst_x1: int
    dst_c0: int
    dst_c1: int
    # Valid output rows/cols inside NPU output (in case tile was padded to uniform size)
    valid_oh: int
    valid_ow: int
    pad_value: int = 0
    accum_mode: str = 'assign'  # 'assign' | 'add_i32'
    flatten_input: bool = False  # True for FC reading a slice of flattened input
    # Second operand of a two-input hardware Add tile: same window as src, read from src2_name.
    src2_name: str | None = None
    src2_pad_value: int = 0

    def to_dict(self) -> dict:
        row = asdict(self)
        if self.src2_name is None:  # keep single-input plans byte-identical to the old schema
            row.pop('src2_name')
            row.pop('src2_pad_value')
        return row


@dataclass
class ProgramGroup:
    """One ABI 5.0 Package loaded ONCE (1 x MODEL_COMMIT) and reused across all TileCalls."""
    program_id: str
    layer_name: str
    package: abi.Package
    sub_model: dict
    calls: list[TileCall]
    is_fused_subgraph: bool = False
    oc_slice: tuple[int, int] | None = None
    k_slice: tuple[int, int] | None = None


@dataclass
class HostPostOp:
    """Host-side exact integer post-op (post-K-tiling requant, oversized residual Add, or CPUFallback)."""
    op: str  # 'Requant' | 'ReLU' | 'Add' | 'CPUFallback'
    inputs: list[str]
    output: str
    attrs: dict = field(default_factory=dict)
    out_shape: tuple[int, int, int] = (1, 1, 1)
    out_dtype: str = 'i4'


@dataclass
class TiledPlan:
    graph: GraphIR
    steps: list[ProgramGroup | HostPostOp] = field(default_factory=list)
    tensor_descs: dict[str, TensorDesc] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    @property
    def groups(self) -> list[ProgramGroup]:
        return [s for s in self.steps if isinstance(s, ProgramGroup)]

    def summary(self) -> dict:
        pgs = self.groups
        host_ops = [s for s in self.steps if isinstance(s, HostPostOp)]
        total_calls = sum(len(g.calls) for g in pgs)
        model_bytes = sum(len(g.package.image) for g in pgs)
        act_in_bytes = sum(len(g.calls) * g.package.manifest['input_bytes'] for g in pgs)
        act_out_bytes = sum(len(g.calls) * g.package.manifest['output_bytes'] for g in pgs)
        fused = sum(1 for g in pgs if g.is_fused_subgraph)
        return {
            'program_groups': len(pgs),
            'tile_calls': total_calls,
            'fused_subgraphs': fused,
            'host_post_ops': len(host_ops),
            'jtag_model_bytes': model_bytes,
            'jtag_input_bytes': act_in_bytes,
            'jtag_output_bytes': act_out_bytes,
            'jtag_total_bytes': model_bytes + act_in_bytes + act_out_bytes,
        }

    def save(self, directory: str | Path) -> None:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        (root / 'graph.json').write_text(json.dumps(self.graph.to_dict(), indent=2) + '\n', encoding='utf-8')
        steps_meta = []
        prog_dir = root / 'programs'
        prog_dir.mkdir(exist_ok=True)
        for idx, step in enumerate(self.steps):
            if isinstance(step, ProgramGroup):
                pdir = prog_dir / f'{idx:04d}_{step.program_id}'
                step.package.save(pdir)
                (pdir / 'sub_model.json').write_text(json.dumps(step.sub_model) + '\n', encoding='utf-8')
                steps_meta.append({
                    'kind': 'program_group',
                    'dir': pdir.name,
                    'program_id': step.program_id,
                    'layer_name': step.layer_name,
                    'is_fused_subgraph': step.is_fused_subgraph,
                    'oc_slice': list(step.oc_slice) if step.oc_slice else None,
                    'k_slice': list(step.k_slice) if step.k_slice else None,
                    'calls': [c.to_dict() for c in step.calls],
                })
            else:
                steps_meta.append({
                    'kind': 'host_post_op',
                    'op': step.op,
                    'inputs': step.inputs,
                    'output': step.output,
                    'attrs': step.attrs,
                    'out_shape': list(step.out_shape),
                    'out_dtype': step.out_dtype,
                })
        plan_doc = {
            'schema': 1,
            'summary': self.summary(),
            'metadata': self.metadata,
            'tensor_descs': {k: asdict(v) for k, v in self.tensor_descs.items()},
            'steps': steps_meta,
        }
        (root / 'plan.json').write_text(json.dumps(plan_doc, indent=2) + '\n', encoding='utf-8')

    @classmethod
    def load(cls, directory: str | Path) -> 'TiledPlan':
        root = Path(directory)
        graph = GraphIR.from_dict(json.loads((root / 'graph.json').read_text(encoding='utf-8')))
        doc = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
        tdescs = {k: TensorDesc(**v) for k, v in doc['tensor_descs'].items()}
        steps: list[ProgramGroup | HostPostOp] = []
        for row in doc['steps']:
            if row['kind'] == 'program_group':
                pdir = root / 'programs' / row['dir']
                pkg = abi.Package.load(pdir)
                sub_model = json.loads((pdir / 'sub_model.json').read_text(encoding='utf-8'))
                calls = [TileCall(**c) for c in row['calls']]
                steps.append(ProgramGroup(
                    program_id=row['program_id'],
                    layer_name=row['layer_name'],
                    package=pkg,
                    sub_model=sub_model,
                    calls=calls,
                    is_fused_subgraph=bool(row.get('is_fused_subgraph', False)),
                    oc_slice=tuple(row['oc_slice']) if row.get('oc_slice') else None,
                    k_slice=tuple(row['k_slice']) if row.get('k_slice') else None,
                ))
            else:
                steps.append(HostPostOp(
                    op=row['op'],
                    inputs=list(row['inputs']),
                    output=row['output'],
                    attrs=dict(row['attrs']),
                    out_shape=tuple(row['out_shape']),
                    out_dtype=row['out_dtype'],
                ))
        return cls(graph=graph, steps=steps, tensor_descs=tdescs, metadata=dict(doc.get('metadata', {})))


def _node_to_abi_dict(n: NodeIR) -> dict:
    row = dict(n.attrs)
    row['op'] = n.op
    row['input'] = n.inputs[0]
    row['output'] = n.output
    if n.op == 'Add':
        row['input2'] = n.inputs[1]
    return row


def _try_compile_subgraph(nodes: list[NodeIR], tensors: dict[str, TensorDesc], external_consumers: dict[str, int],
                          *, cols: int, act_bytes: int, model_bytes: int, max_ops: int) -> tuple[abi.Package, dict] | None:
    """Attempt to compile a contiguous slice of NPU nodes as ONE fused ABI 5.0 Package."""
    if not nodes:
        return None
    produced: set[str] = set()
    ext_inputs: list[str] = []
    for n in nodes:
        if n.backend != 'npu' or n.op == 'CPUFallback':
            return None
        # Hardware MaxPool/AvgPool cannot have non-zero pads
        if n.op in ('MaxPool', 'AvgPool') and any(int(v) != 0 for v in n.attrs.get('pads', [0, 0, 0, 0])):
            return None
        for inp in n.inputs:
            if inp not in produced and inp not in ext_inputs:
                ext_inputs.append(inp)
        produced.add(n.output)
    # Single external input, single final external output (no mid-subgraph outputs escaping!)
    if len(ext_inputs) != 1:
        return None
    final_out = nodes[-1].output
    for n in nodes[:-1]:
        if external_consumers.get(n.output, 0) > 0:
            return None
    in_name = ext_inputs[0]
    in_td = tensors[in_name]
    sub_model = {
        'input': {'name': in_name, 'shape': list(in_td.shape), 'dtype': in_td.dtype},
        'nodes': [_node_to_abi_dict(n) for n in nodes],
        'output': final_out,
    }
    try:
        pkg = abi.compile_model(sub_model, fuse=True, cols=cols, act_bytes=act_bytes,
                                model_bytes=model_bytes, max_ops=max_ops)
        return pkg, sub_model
    except ValueError:
        return None




# ---------------------------------------------------------------------------
# Shared tiling machinery
# ---------------------------------------------------------------------------
SRAM_BYTES = 16384          # 2 x 8192 B shared activation + model-tail SRAM (ABI 5.0)
DESC_BYTES = 64
# Cost-model surcharges (in "JTAG byte equivalents") for the USB round trips of one
# MODEL_BEGIN..COMMIT and of one INPUT..START..poll..ACK sequence.
GROUP_PENALTY_BYTES = 256
CALL_PENALTY_BYTES = 256
P_FUSED_POOLS = ((2, 2, 2, 2), (1, 2, 1, 2))  # pool shapes the core fuses into the Conv descriptor


@dataclass
class _Ctx:
    cols: int
    act_bytes: int
    model_bytes: int
    max_ops: int
    counter: list[int]

    def new_id(self) -> str:
        pid = f'p{self.counter[0]:04d}'
        self.counter[0] += 1
        return pid

    def compile(self, sub_model: dict) -> abi.Package:
        return abi.compile_model(sub_model, fuse=True, cols=self.cols, act_bytes=self.act_bytes,
                                 model_bytes=self.model_bytes, max_ops=self.max_ops)


@dataclass(frozen=True)
class _Window:
    """One sliding-window operator mapping an output tile back to its input tile."""
    kh: int
    kw: int
    sh: int
    sw: int
    pt: int = 0
    pl: int = 0

    def in_size(self, oh: int, ow: int) -> tuple[int, int]:
        return (oh - 1) * self.sh + self.kh, (ow - 1) * self.sw + self.kw

    def in_start(self, oy: int, ox: int) -> tuple[int, int]:
        return oy * self.sh - self.pt, ox * self.sw - self.pl


_IDENTITY = _Window(1, 1, 1, 1)


def _chain_size(windows: list[_Window], oh: int, ow: int) -> tuple[int, int]:
    for wdw in windows:  # ordered output -> input
        oh, ow = wdw.in_size(oh, ow)
    return oh, ow


def _chain_start(windows: list[_Window], oy: int, ox: int) -> tuple[int, int]:
    for wdw in windows:
        oy, ox = wdw.in_start(oy, ox)
    return oy, ox


def _tensor_bytes(h: int, w: int, c: int, dtype: str) -> int:
    n = h * w * c
    return n * 4 if dtype == 'i32' else (n + 1) // 2


def _estimate_conv_model_bytes(kh: int, kw: int, ic_tile: int, oc_tile: int, depthwise: bool,
                               cols: int = 8) -> int:
    """Exact ABI 5.0 model image size in bytes for a single Conv/FC (+ fused Requant/ReLU)."""
    if depthwise:
        weight_nibbles = abi.align(oc_tile, cols) * kh * kw
    else:
        k = kh * kw * ic_tile
        row_pad = (ic_tile == 1 and kw <= 8 and kh > 1)
        k_padded = kh * 8 if row_pad else abi.align(k, 8)
        weight_nibbles = 0
        for o_base in range(0, oc_tile, cols):
            width = 4 if cols == 8 and oc_tile - o_base <= 4 else cols
            weight_nibbles += k_padded * width
    return DESC_BYTES + abi.align((weight_nibbles + 1) // 2, 4) + oc_tile * 4


def _channel_candidates(c: int, cols: int, limit: int = MAX_OC) -> list[int]:
    """Balanced channel tiles: ceil(c/n) rounded up to whole array-column groups."""
    out = set()
    for n in range(1, math.ceil(c / min(cols, c)) + 1):
        t = math.ceil(c / n)
        if t != c and c > cols:
            t = abi.align(t, cols)
        if t <= limit:
            out.add(t)
    if c > limit:
        out.add(limit // cols * cols)
    return sorted(out, reverse=True)


def _k_candidates(k: int, limit: int) -> list[int]:
    """Balanced reduction-dimension tiles <= limit, multiples of 8 when splitting."""
    out = set()
    if k <= limit:
        out.add(k)
    n0 = max(1, math.ceil(k / limit))
    for n in range(n0, 4 * n0 + 8):
        t = math.ceil(k / n)
        if t >= 8:
            t = abi.align(t, 8)
            if t > limit:
                t = limit // 8 * 8
        if 1 <= t < k:
            out.add(t)
    for t in (128, 112, 96, 64, 56, 48, 32, 16, 8):
        if t < k and t <= limit:
            out.add(t)
    return sorted(out, reverse=True)


def _best_spatial(rh: int, rw: int, need_fn) -> tuple[int, int, int, int] | None:
    """Minimum-transfer (cost, th, tw, tiles) uniform tiling of an rh x rw output region.

    need_fn(th, tw) returns None when the tile is illegal (SRAM / 255 limits), else the
    per-call JTAG transfer bytes. Legality must be monotone in th."""
    best = None
    seen = set()
    for nw in range(1, rw + 1):
        tw = math.ceil(rw / nw)
        if tw in seen:
            continue
        seen.add(tw)
        lo, hi, ok = 1, rh, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if need_fn(mid, tw) is not None:
                ok, lo = mid, mid + 1
            else:
                hi = mid - 1
        if not ok:
            continue
        th = math.ceil(rh / math.ceil(rh / ok))  # balance rows so the last tile wastes least
        tiles = math.ceil(rh / th) * math.ceil(rw / tw)
        cost = tiles * (need_fn(th, tw) + CALL_PENALTY_BYTES)
        if best is None or cost < best[0]:
            best = (cost, th, tw, tiles)
    return best


def _requant_slice(req: NodeIR, c0: int, c1: int, c: int) -> dict:
    ra = req.attrs
    mm, ss = list(ra['multiplier']), list(ra['shift'])
    mm = mm * c if len(mm) == 1 else mm
    ss = ss * c if len(ss) == 1 else ss
    dtype = ra.get('dtype', 'i4')
    return dict(multiplier=mm[c0:c1], shift=ss[c0:c1], dtype=dtype,
                clamp=list(ra.get('clamp', [0, 15] if dtype == 'u4' else [-8, 7])))


def _append_post_ops(nodes: list[dict], cur: str, req_sub: dict | None, relu: bool) -> str:
    if req_sub is not None:
        nodes.append(dict(op='Requant', input=cur, output='req', **req_sub))
        cur = 'req'
    if relu:
        nodes.append(dict(op='ReLU', input=cur, output='relu'))
        cur = 'relu'
    return cur


def _host_post_ops(post_req: NodeIR | None, post_relu: NodeIR | None, src: str,
                   tensors: dict[str, TensorDesc]) -> list[HostPostOp]:
    steps = []
    for node in (post_req, post_relu):
        if node is None:
            continue
        td = tensors[node.output]
        steps.append(HostPostOp(op=node.op, inputs=[src] if node is post_req else list(node.inputs),
                                output=node.output, attrs=dict(node.attrs), out_shape=td.shape,
                                out_dtype=td.dtype))
        src = node.output
    return steps


def _region_calls(src: str, dst: str, region, th: int, tw: int, windows: list[_Window],
                  c_src: tuple[int, int], c_dst: tuple[int, int], *, pad_value: int = 0,
                  accum_mode: str = 'assign', src2: str | None = None) -> list[TileCall]:
    ry0, ry1, rx0, rx1 = region
    ih, iw = _chain_size(windows, th, tw)
    calls = []
    for oy0 in range(ry0, ry1, th):
        oy1 = min(ry1, oy0 + th)
        for ox0 in range(rx0, rx1, tw):
            ox1 = min(rx1, ox0 + tw)
            iy0, ix0 = _chain_start(windows, oy0, ox0)
            calls.append(TileCall(
                src_name=src, dst_name=dst,
                src_y0=iy0, src_y1=iy0 + ih, src_x0=ix0, src_x1=ix0 + iw, src_c0=c_src[0], src_c1=c_src[1],
                dst_y0=oy0, dst_y1=oy1, dst_x0=ox0, dst_x1=ox1, dst_c0=c_dst[0], dst_c1=c_dst[1],
                valid_oh=oy1 - oy0, valid_ow=ox1 - ox0, pad_value=pad_value, accum_mode=accum_mode,
                src2_name=src2, src2_pad_value=0,
            ))
    return calls


def _steps_cost(steps: list) -> int:
    cost = 0
    for s in steps:
        if isinstance(s, ProgramGroup):
            m = s.package.manifest
            cost += len(s.package.image) + GROUP_PENALTY_BYTES
            cost += len(s.calls) * (m['input_bytes'] + m['output_bytes'] + CALL_PENALTY_BYTES)
        else:
            cost += 4 * math.prod(s.out_shape)  # host work on a full tensor
    return cost


# ---------------------------------------------------------------------------
# Conv (+ Requant + ReLU [+ MaxPool]) tiling
# ---------------------------------------------------------------------------
@dataclass
class _ConvGeo:
    src: str
    h: int
    w: int
    ic: int
    oc: int
    oh: int
    ow: int
    depthwise: bool
    win: _Window
    weights: np.ndarray
    bias: np.ndarray


def _conv_geo(conv: NodeIR, tensors: dict[str, TensorDesc]) -> _ConvGeo:
    a = conv.attrs
    src = conv.inputs[0]
    h, w, ic = tensors[src].shape
    oc = int(a['channels'])
    kh, kw = abi.pair(a.get('kernel', 3), 'kernel')
    sh, sw = abi.pair(a.get('stride', 1), 'stride')
    pt, pl, _, _ = [int(v) for v in a.get('pads', [0, 0, 0, 0])]
    depthwise = int(a.get('groups', 1)) != 1
    oh, ow, _ = tensors[conv.output].shape
    wts = np.asarray(a['weights'], dtype=np.int64).reshape((oc, kh, kw) if depthwise else (oc, kh, kw, ic))
    bias = np.asarray(a.get('bias', [0] * oc), dtype=np.int64)
    return _ConvGeo(src, h, w, ic, oc, oh, ow, depthwise, _Window(kh, kw, sh, sw, pt, pl), wts, bias)


def _pool_window(pool: NodeIR) -> _Window:
    kh, kw = abi.pair(pool.attrs.get('kernel', 2), 'kernel')
    sh, sw = abi.pair(pool.attrs.get('stride', [kh, kw]), 'stride')
    pt, pl, _, _ = [int(v) for v in pool.attrs.get('pads', [0, 0, 0, 0])]
    return _Window(kh, kw, sh, sw, pt, pl)


def _search_conv(g: _ConvGeo, in_dtype: str, final_dtype: str, region, ctx: _Ctx, *,
                 pool: _Window | None = None, mid_dtype: str | None = None,
                 allow_k_tiling: bool = True):
    """Joint (ic_tile, oc_tile, oh_tile, ow_tile) search minimizing JTAG bytes + round trips.

    Returns (cost, ic_t, oc_t, th, tw) or None. Region is in output coordinates of the
    last operator (the pool when one is fused)."""
    rh, rw = region[1] - region[0], region[3] - region[2]
    kh, kw = g.win.kh, g.win.kw
    p_fused = pool is not None and (pool.kh, pool.kw, pool.sh, pool.sw) in P_FUSED_POOLS
    ic_cands = [g.ic] if g.depthwise else _k_candidates(g.ic, MAX_K // (kh * kw))
    if not allow_k_tiling:
        ic_cands = [c for c in ic_cands if c == g.ic]
    best = None
    for ic_t in ic_cands:
        k_tiled = ic_t < g.ic
        out_dtype = 'i32' if k_tiled else final_dtype
        n_k = math.ceil(g.ic / ic_t)
        for oc_t in _channel_candidates(g.oc, ctx.cols):
            if g.depthwise and oc_t % ctx.cols:
                continue
            c_in = oc_t if g.depthwise else ic_t
            m = _estimate_conv_model_bytes(kh, kw, c_in, oc_t, g.depthwise, ctx.cols)
            if pool is not None and not p_fused:
                m += DESC_BYTES
            if m > ctx.model_bytes:
                continue
            usable = SRAM_BYTES - m

            def need(th, tw, c_in=c_in, oc_t=oc_t, usable=usable, out_dtype=out_dtype):
                if th > MAX_SPATIAL or tw > MAX_SPATIAL:
                    return None
                ch, cw = pool.in_size(th, tw) if pool is not None else (th, tw)
                ih, iw = g.win.in_size(ch, cw)
                if max(ch, cw, ih, iw) > MAX_SPATIAL:
                    return None
                ib = _tensor_bytes(ih, iw, c_in, in_dtype)
                ob = _tensor_bytes(th, tw, oc_t, out_dtype)
                total = abi.align(ib) + abi.align(ob)
                if pool is not None and not p_fused:
                    total += abi.align(_tensor_bytes(ch, cw, oc_t, mid_dtype))
                return ib + ob if total <= usable else None

            sp = _best_spatial(rh, rw, need)
            if sp is None:
                continue
            cost = math.ceil(g.oc / oc_t) * n_k * (m + GROUP_PENALTY_BYTES + sp[0])
            if best is None or cost < best[0]:
                best = (cost, ic_t, oc_t, sp[1], sp[2])
    return best


def _choose_conv_tiling(h: int, w: int, ic: int, oc: int, kh: int, kw: int, sh: int, sw: int,
                        oh: int, ow: int, depthwise: bool, in_dtype: str, out_dtype_single_k: str,
                        *, cols: int, act_bytes: int, model_bytes: int) -> tuple[int, int, int, int, bool]:
    """(ic_tile, oc_tile, oh_tile, ow_tile, k_tiled) for a full Conv output (cost-model search)."""
    g = _ConvGeo('x', h, w, ic, oc, oh, ow, depthwise, _Window(kh, kw, sh, sw), np.zeros(0), np.zeros(0))
    best = _search_conv(g, in_dtype, out_dtype_single_k, (0, oh, 0, ow),
                        _Ctx(cols, act_bytes, model_bytes, 128, [0]))
    if best is None:
        raise ValueError(f'Unable to tile Conv ({h}x{w}x{ic} -> oc={oc}, k={kh}x{kw}) into ABI 5.0 SRAM')
    _, ic_t, oc_t, th, tw = best
    return ic_t, oc_t, th, tw, ic_t < ic


def _emit_conv(g: _ConvGeo, conv: NodeIR, post_req: NodeIR | None, post_relu: bool, pool: NodeIR | None,
               in_dtype: str, dst: str, region, cfg, ctx: _Ctx) -> list[ProgramGroup]:
    _, ic_t, oc_t, th, tw = cfg
    k_tiled = ic_t < g.ic
    pwin = _pool_window(pool) if pool is not None else None
    windows = ([pwin] if pwin else []) + [g.win]
    ch, cw = pwin.in_size(th, tw) if pwin else (th, tw)
    ih, iw = g.win.in_size(ch, cw)
    steps = []
    for oc0 in range(0, g.oc, oc_t):
        oc1 = min(g.oc, oc0 + oc_t)
        k_ranges = [(oc0, oc1)] if g.depthwise else [(k0, min(g.ic, k0 + ic_t)) for k0 in range(0, g.ic, ic_t)]
        for k_idx, (k0, k1) in enumerate(k_ranges):
            w_sub = g.weights[oc0:oc1] if g.depthwise else g.weights[oc0:oc1, :, :, k0:k1]
            nodes = [dict(op='Conv', input='x', output='conv', channels=oc1 - oc0,
                          kernel=[g.win.kh, g.win.kw], stride=[g.win.sh, g.win.sw], pads=[0, 0, 0, 0],
                          groups=(oc1 - oc0) if g.depthwise else 1,
                          weights=w_sub.reshape(-1).tolist(),
                          bias=g.bias[oc0:oc1].tolist() if k_idx == 0 else [0] * (oc1 - oc0))]
            cur = 'conv'
            if not k_tiled:
                req_sub = _requant_slice(post_req, oc0, oc1, g.oc) if post_req is not None else None
                cur = _append_post_ops(nodes, cur, req_sub, post_relu)
            if pwin is not None:
                nodes.append(dict(op='MaxPool', input=cur, output='pool', kernel=[pwin.kh, pwin.kw],
                                  stride=[pwin.sh, pwin.sw], pads=[0, 0, 0, 0]))
                cur = 'pool'
            sub_model = {'input': {'name': 'x', 'shape': [ih, iw, k1 - k0], 'dtype': in_dtype},
                         'nodes': nodes, 'output': cur}
            calls = _region_calls(g.src, dst, region, th, tw, windows, (k0, k1), (oc0, oc1),
                                  accum_mode='add_i32' if k_idx else 'assign')
            steps.append(ProgramGroup(
                program_id=ctx.new_id(), layer_name=conv.name + ('+' + pool.name if pool is not None else ''),
                package=ctx.compile(sub_model), sub_model=sub_model, calls=calls,
                is_fused_subgraph=pool is not None, oc_slice=(oc0, oc1), k_slice=(k0, k1)))
    return steps


def _tile_conv_layer(conv: NodeIR, post_req: NodeIR | None, post_relu: NodeIR | None,
                     tensors: dict[str, TensorDesc], ctx: _Ctx, *, region=None, dst: str | None = None,
                     allow_k_tiling: bool = True) -> list[ProgramGroup | HostPostOp] | None:
    """Conv [+Requant][+ReLU] over an output region (default: whole tensor)."""
    g = _conv_geo(conv, tensors)
    final = post_relu or post_req or conv
    region = region or (0, g.oh, 0, g.ow)
    in_dtype = tensors[g.src].dtype
    cfg = _search_conv(g, in_dtype, tensors[final.output].dtype, region, ctx, allow_k_tiling=allow_k_tiling)
    if cfg is None:
        if not allow_k_tiling:
            return None
        raise ValueError(f'Unable to tile Conv {conv.name} ({g.h}x{g.w}x{g.ic} -> {g.oc}) into ABI 5.0 SRAM')
    k_tiled = cfg[1] < g.ic
    out = conv.output if k_tiled else (dst or final.output)
    steps: list = _emit_conv(g, conv, post_req, post_relu is not None, None, in_dtype, out, region, cfg, ctx)
    if k_tiled:
        steps += _host_post_ops(post_req, post_relu, conv.output, tensors)
    return steps


def _pool_regions(oh: int, ow: int, ch: int, cw: int, win: _Window):
    """Split pool outputs into the unpadded interior and the (padded) border strips."""
    def span(n_out, n_in, k, s, p):
        lo = min(n_out, math.ceil(p / s))
        hi = max(lo, min(n_out, (n_in + p - k) // s + 1))
        return lo, hi
    y0, y1 = span(oh, ch, win.kh, win.sh, win.pt)
    x0, x1 = span(ow, cw, win.kw, win.sw, win.pl)
    interior = (y0, y1, x0, x1)
    borders = [r for r in ((0, y0, 0, ow), (y1, oh, 0, ow), (y0, y1, 0, x0), (y0, y1, x1, ow))
               if r[1] > r[0] and r[3] > r[2]]
    return interior, borders


def _pool_input_region(region, win: _Window, ch: int, cw: int):
    y0, y1, x0, x1 = region
    return (max(0, y0 * win.sh - win.pt), min(ch, (y1 - 1) * win.sh - win.pt + win.kh),
            max(0, x0 * win.sw - win.pl), min(cw, (x1 - 1) * win.sw - win.pl + win.kw))


def _tile_conv_pool_fused(conv: NodeIR, post_req: NodeIR | None, post_relu: NodeIR | None, pool: NodeIR,
                          tensors: dict[str, TensorDesc], ctx: _Ctx) -> list | None:
    """Halo-fused Conv [+Requant][+ReLU] -> MaxPool: the conv intermediate stays in SRAM for the
    unpadded interior; the thin padded border is computed as conv strips + padded pool tiles."""
    g = _conv_geo(conv, tensors)
    mid = (post_relu or post_req or conv).output
    mid_dtype = tensors[mid].dtype
    if mid_dtype not in abi.NIBBLE:
        return None
    pwin = _pool_window(pool)
    poh, pow_, _ = tensors[pool.output].shape
    interior, borders = _pool_regions(poh, pow_, g.oh, g.ow, pwin)
    if interior[1] <= interior[0] or interior[3] <= interior[2]:
        return None
    in_dtype = tensors[g.src].dtype
    cfg = _search_conv(g, in_dtype, tensors[pool.output].dtype, interior, ctx,
                       pool=pwin, mid_dtype=mid_dtype, allow_k_tiling=False)
    if cfg is None:
        return None
    steps: list = _emit_conv(g, conv, post_req, post_relu is not None, pool, in_dtype,
                             pool.output, interior, cfg, ctx)
    for border in borders:
        conv_region = _pool_input_region(border, pwin, g.oh, g.ow)
        strip = _tile_conv_layer(conv, post_req, post_relu, tensors, ctx, region=conv_region,
                                 dst=mid, allow_k_tiling=False)
        if strip is None:
            return None
        steps += strip
        steps += _tile_pool_layer(pool, None, None, tensors, ctx, region=border)
    return steps


# ---------------------------------------------------------------------------
# FC, pooling and residual Add tiling
# ---------------------------------------------------------------------------
def _choose_fc_tiling(k_in: int, oc: int, in_dtype: str, final_dtype: str, ctx: _Ctx):
    """Joint (k_tile, oc_tile) minimizing JTAG bytes + program groups. Returns (cost, k_t, oc_t)."""
    best = None
    for k_t in _k_candidates(k_in, MAX_K):
        out_dtype = 'i32' if k_t < k_in else final_dtype
        n_k = math.ceil(k_in / k_t)
        for oc_t in _channel_candidates(oc, ctx.cols):
            m = _estimate_conv_model_bytes(1, 1, k_t, oc_t, False, ctx.cols)
            ib = _tensor_bytes(1, 1, k_t, in_dtype)
            ob = _tensor_bytes(1, 1, oc_t, out_dtype)
            if m > ctx.model_bytes or m + abi.align(ib) + abi.align(ob) > SRAM_BYTES:
                continue
            cost = math.ceil(oc / oc_t) * n_k * (m + GROUP_PENALTY_BYTES + ib + ob + CALL_PENALTY_BYTES)
            if best is None or cost < best[0]:
                best = (cost, k_t, oc_t)
    if best is None:
        raise ValueError(f'Unable to tile FC (K={k_in}, OC={oc}) into ABI 5.0 SRAM')
    return best


def _tile_fc_layer(fc: NodeIR, post_req: NodeIR | None, post_relu: NodeIR | None,
                   tensors: dict[str, TensorDesc], ctx: _Ctx) -> list[ProgramGroup | HostPostOp]:
    src = fc.inputs[0]
    t_in = tensors[src]
    k_in = math.prod(t_in.shape)
    oc = int(fc.attrs['channels'])
    w_full = np.asarray(fc.attrs['weights'], dtype=np.int64).reshape(oc, k_in)
    b_full = np.asarray(fc.attrs.get('bias', [0] * oc), dtype=np.int64)
    final = post_relu or post_req or fc
    _, k_t, oc_t = _choose_fc_tiling(k_in, oc, t_in.dtype, tensors[final.output].dtype, ctx)
    k_tiled = k_t < k_in
    dst = fc.output if k_tiled else final.output
    steps: list = []
    for oc0 in range(0, oc, oc_t):
        oc1 = min(oc, oc0 + oc_t)
        for k_idx, k0 in enumerate(range(0, k_in, k_t)):
            k1 = min(k_in, k0 + k_t)
            nodes = [dict(op='FC', input='x', output='fc', channels=oc1 - oc0,
                          weights=w_full[oc0:oc1, k0:k1].reshape(-1).tolist(),
                          bias=b_full[oc0:oc1].tolist() if k_idx == 0 else [0] * (oc1 - oc0))]
            cur = 'fc'
            if not k_tiled:
                req_sub = _requant_slice(post_req, oc0, oc1, oc) if post_req is not None else None
                cur = _append_post_ops(nodes, cur, req_sub, post_relu is not None)
            sub_model = {'input': {'name': 'x', 'shape': [1, 1, k1 - k0], 'dtype': t_in.dtype},
                         'nodes': nodes, 'output': cur}
            call = TileCall(src_name=src, dst_name=dst, src_y0=0, src_y1=1, src_x0=0, src_x1=1,
                            src_c0=k0, src_c1=k1, dst_y0=0, dst_y1=1, dst_x0=0, dst_x1=1,
                            dst_c0=oc0, dst_c1=oc1, valid_oh=1, valid_ow=1,
                            accum_mode='add_i32' if k_idx else 'assign', flatten_input=True)
            steps.append(ProgramGroup(program_id=ctx.new_id(), layer_name=fc.name, package=ctx.compile(sub_model),
                                      sub_model=sub_model, calls=[call], oc_slice=(oc0, oc1), k_slice=(k0, k1)))
    if k_tiled:
        steps += _host_post_ops(post_req, post_relu, fc.output, tensors)
    return steps


def _tile_pool_layer(pool: NodeIR, post_req: NodeIR | None, post_relu: NodeIR | None,
                     tensors: dict[str, TensorDesc], ctx: _Ctx, *, region=None) -> list[ProgramGroup]:
    """MaxPool / AvgPool [+Requant][+ReLU]; non-zero pads are applied by host tile padding."""
    src = pool.inputs[0]
    t_in = tensors[src]
    c = t_in.shape[2]
    win = _pool_window(pool)
    final = post_relu or post_req or pool
    out_dtype = tensors[final.output].dtype
    oh, ow, _ = tensors[pool.output].shape
    region = region or (0, oh, 0, ow)
    best = None
    for c_t in _channel_candidates(c, ctx.cols):
        m = DESC_BYTES + (4 * c_t if post_req is not None else 0)

        def need(th, tw, c_t=c_t, m=m):
            ih, iw = win.in_size(th, tw)
            if max(th, tw, ih, iw) > MAX_SPATIAL:
                return None
            ib = _tensor_bytes(ih, iw, c_t, t_in.dtype)
            ob = _tensor_bytes(th, tw, c_t, out_dtype)
            return ib + ob if m + abi.align(ib) + abi.align(ob) <= SRAM_BYTES else None

        sp = _best_spatial(region[1] - region[0], region[3] - region[2], need)
        if sp is not None:
            cost = math.ceil(c / c_t) * (m + GROUP_PENALTY_BYTES + sp[0])
            if best is None or cost < best[0]:
                best = (cost, c_t, sp[1], sp[2])
    if best is None:
        raise ValueError(f'Unable to tile {pool.op} {pool.name} into ABI 5.0 SRAM')
    _, c_t, th, tw = best
    ih, iw = win.in_size(th, tw)
    pad = pad_value_for_dtype(t_in.dtype, pool.op)
    steps = []
    for c0 in range(0, c, c_t):
        c1 = min(c, c0 + c_t)
        nodes = [dict(op=pool.op, input='x', output='p', kernel=[win.kh, win.kw], stride=[win.sh, win.sw],
                      pads=[0, 0, 0, 0])]
        req_sub = _requant_slice(post_req, c0, c1, c) if post_req is not None else None
        cur = _append_post_ops(nodes, 'p', req_sub, post_relu is not None)
        sub_model = {'input': {'name': 'x', 'shape': [ih, iw, c1 - c0], 'dtype': t_in.dtype},
                     'nodes': nodes, 'output': cur}
        calls = _region_calls(src, final.output, region, th, tw, [win], (c0, c1), (c0, c1), pad_value=pad)
        steps.append(ProgramGroup(program_id=ctx.new_id(), layer_name=pool.name, package=ctx.compile(sub_model),
                                  sub_model=sub_model, calls=calls, oc_slice=(c0, c1)))
    return steps


def _add_layout(th: int, tw: int, c: int, out_dtype: str) -> tuple[int, int, int, int, int]:
    """(in1_bytes, in2_bytes, src2, dst, out_bytes) for one Add tile; 4-bit output runs in place on src2."""
    b1 = _tensor_bytes(th, tw, c, 'i4')
    b2 = b1
    out_b = _tensor_bytes(th, tw, c, out_dtype)
    src2 = abi.align(b1)
    dst = src2 if out_dtype in abi.NIBBLE else src2 + abi.align(b2)
    return b1, b2, src2, dst, out_b


def build_add_package(shape: tuple[int, int, int], dtypes: tuple[str, str], weights: tuple[int, int],
                      requant: dict | None, relu: bool, *, cols: int = 8) -> abi.Package:
    """One-descriptor ABI 5.0 OP_ADD=7 package reading in1 at 0 and in2 at align4(in1_bytes).

    The host sends one contiguous payload ``in1 || zero pad to 4 B || in2``. With a 4-bit
    output the result overwrites in2 exactly in place (RTL permits src2 == dst with equal
    sizes); an i32 output follows in2."""
    h, w, c = shape
    wa, wb = (int(v) for v in weights)
    if not (-128 <= wa <= 127 and -128 <= wb <= 127):
        raise ValueError('Add weights must be signed 8-bit')
    if any(d not in abi.NIBBLE for d in dtypes):
        raise ValueError('hardware Add needs i4/u4 inputs')
    out_dtype = requant['dtype'] if requant is not None else 'i32'
    b1, b2, src2, dst, out_b = _add_layout(h, w, c, out_dtype)
    flags = abi.OPS['Add'] | (abi.SU if dtypes[0] == 'u4' else 0) | (abi.SU2 if dtypes[1] == 'u4' else 0)
    flags |= abi.D32 if out_dtype == 'i32' else (abi.DU if out_dtype == 'u4' else 0)
    flags |= (abi.Q if requant is not None else 0) | (abi.R if relu else 0)
    image = bytearray(DESC_BYTES)
    param_base, clamp = 0, (0, 0)
    if requant is not None:
        param_base = DESC_BYTES
        clamp = requant['clamp']
        image += struct.pack(f'<{c}I', *(abi.param_word(0, m, s)
                                          for m, s in zip(requant['multiplier'], requant['shift'])))
    words = [flags, 0, dst, h | (w << 16), c | (c << 16), 0, 0, 0, param_base,
             (clamp[0] & 31) | ((clamp[1] & 31) << 5), h | (w << 16), src2,
             (wa & 255) | ((wb & 255) << 8), 0, 0, abi.FORMAT_MAGIC | cols]
    struct.pack_into('<16I', image, 0, *words)
    in_bytes = src2 + b2
    if len(image) > SRAM_BYTES // 2 or len(image) + max(in_bytes, dst + out_b) > SRAM_BYTES:
        raise ValueError('Add tile exceeds ABI 5.0 shared SRAM')
    shape_l = [h, w, c]
    out_name = 'relu' if relu else ('req' if requant is not None else 'add')  # see _add_sub_model
    tensors = {'a': dict(base=0, bytes=b1, shape=shape_l, dtype=dtypes[0]),
               'b': dict(base=src2, bytes=b2, shape=shape_l, dtype=dtypes[1]),
               out_name: dict(base=dst, bytes=out_b, shape=shape_l, dtype=out_dtype)}
    manifest = dict(rows=8, activation_bank_bytes=SRAM_BYTES // 2, shared_model_tail=True, abi=abi.ABI,
                    numeric_format=abi.NUMERIC_FORMAT, packing='low_nibble_first', cols=cols,
                    model_bytes=len(image), op_count=1, input_elements=2 * h * w * c,
                    input_base=0, input_bytes=in_bytes, input_dtype=dtypes[0],
                    input_tensors=['a', 'b'],
                    output_base=dst, output_bytes=out_b, output_elements=h * w * c,
                    output_dtype=out_dtype, output_shape=shape_l,
                    activation_peak=max(in_bytes, dst + out_b),
                    groups=[['Add'] + (['Requant'] if requant is not None else []) + (['ReLU'] if relu else [])],
                    tensors=tensors, metadata={})
    return abi.Package(bytes(image), manifest)


def _add_sub_model(shape, dtypes, weights, requant: dict | None, relu: bool) -> dict:
    nodes = [dict(op='Add', input='a', input2='b', output='add', weights=list(weights))]
    cur = _append_post_ops(nodes, 'add', requant, relu)
    return {'input': {'name': 'a', 'shape': list(shape), 'dtype': dtypes[0]},
            'input2': {'name': 'b', 'shape': list(shape), 'dtype': dtypes[1]},
            'nodes': nodes, 'output': cur}


def _tile_add_layer(add: NodeIR, post_req: NodeIR | None, post_relu: NodeIR | None,
                    tensors: dict[str, TensorDesc], ctx: _Ctx) -> list | None:
    """Residual Add [+Requant][+ReLU] on the NPU (OP_ADD=7), spatially and channel tiled."""
    a_name, b_name = add.inputs
    ta, tb = tensors[a_name], tensors[b_name]
    weights = [int(v) for v in add.attrs.get('weights', [1, 1])]
    if ta.dtype not in abi.NIBBLE or tb.dtype not in abi.NIBBLE or ta.shape != tb.shape or \
            not all(-128 <= v <= 127 for v in weights):
        return None
    final = post_relu or post_req or add
    out_dtype = tensors[final.output].dtype
    h, w, c = ta.shape
    best = None
    for c_t in _channel_candidates(c, ctx.cols):
        m = DESC_BYTES + (4 * c_t if post_req is not None else 0)

        def need(th, tw, c_t=c_t, m=m):
            if th > MAX_SPATIAL or tw > MAX_SPATIAL:
                return None
            b1, b2, src2, dst, out_b = _add_layout(th, tw, c_t, out_dtype)
            return b1 + b2 + out_b if m + max(src2 + b2, dst + out_b) <= SRAM_BYTES else None

        sp = _best_spatial(h, w, need)
        if sp is not None:
            cost = math.ceil(c / c_t) * (m + GROUP_PENALTY_BYTES + sp[0])
            if best is None or cost < best[0]:
                best = (cost, c_t, sp[1], sp[2])
    if best is None:
        return None
    _, c_t, th, tw = best
    steps = []
    for c0 in range(0, c, c_t):
        c1 = min(c, c0 + c_t)
        req_sub = _requant_slice(post_req, c0, c1, c) if post_req is not None else None
        shape = (th, tw, c1 - c0)
        pkg = build_add_package(shape, (ta.dtype, tb.dtype), weights, req_sub, post_relu is not None,
                                cols=ctx.cols)
        calls = _region_calls(a_name, final.output, (0, h, 0, w), th, tw, [_IDENTITY], (c0, c1), (c0, c1),
                              src2=b_name)
        steps.append(ProgramGroup(program_id=ctx.new_id(), layer_name=add.name, package=pkg,
                                  sub_model=_add_sub_model(shape, (ta.dtype, tb.dtype), weights, req_sub,
                                                           post_relu is not None),
                                  calls=calls, is_fused_subgraph=final is not add, oc_slice=(c0, c1)))
    return steps


# ---------------------------------------------------------------------------
# Graph-level planner
# ---------------------------------------------------------------------------
def _quick_subgraph_reject(chunk: list[NodeIR], tensors: dict[str, TensorDesc], model_bytes: int) -> bool:
    """Cheap lower bound on the model image so whole-subgraph compile attempts stop once it cannot fit.
    (Activation sizes are left to compile_model: fused Requants never materialize their i32 input.)"""
    weight_bytes = DESC_BYTES * len(chunk)
    for n in chunk:
        if n.op in ('Conv', 'FC'):
            weight_bytes += len(n.attrs['weights']) // 2 + 4 * int(n.attrs['channels'])
    return weight_bytes > model_bytes


def _single_consumer(nodes: list[NodeIR], idx: int, name: str, use_count: dict[str, int], op: str) -> bool:
    return idx < len(nodes) and nodes[idx].op == op and nodes[idx].inputs == [name] and use_count.get(name, 0) == 1


def compile_graph_to_plan(graph: GraphIR, *, cols: int = 8, act_bytes: int = 8192,
                          model_bytes: int = 8192, max_ops: int = 128,
                          enable_fusion: bool = True, hardware_add: bool = True) -> TiledPlan:
    """Compile a GraphIR into a weight-stationary TiledPlan for ABI 5.0 (8 cols, 2 x 8192 B SRAM).

    hardware_add: run residual Add [+Requant][+ReLU] that is not part of a whole fused subgraph
    on the NPU (OP_ADD=7). Both operands then cross JTAG; False keeps it as an exact host op."""
    if cols != 8 or act_bytes + model_bytes != SRAM_BYTES:
        raise ValueError('ABI 5.0 requires 8 columns and 2 x 8192 B shared SRAM')
    tensors = infer_tensors(graph)
    nodes = graph.nodes
    n_total = len(nodes)
    use_count: dict[str, int] = {graph.output: 1}
    for n in nodes:
        for inp in n.inputs:
            use_count[inp] = use_count.get(inp, 0) + 1
    ctx = _Ctx(cols, act_bytes, model_bytes, max_ops, [0])
    steps: list[ProgramGroup | HostPostOp] = []
    i = 0
    while i < n_total:
        # 1) Whole multi-layer subgraph in one Package when it fits.
        if enable_fusion and nodes[i].backend == 'npu' and nodes[i].op != 'CPUFallback':
            best_end, best_compiled = -1, None
            for j in range(i + 1, min(n_total, i + max_ops) + 1):
                chunk = nodes[i:j]
                if chunk[-1].backend != 'npu' or chunk[-1].op == 'CPUFallback' or \
                        _quick_subgraph_reject(chunk, tensors, model_bytes):
                    break
                internal: dict[str, int] = {}
                for nd in chunk:
                    for inp in nd.inputs:
                        internal[inp] = internal.get(inp, 0) + 1
                ext_uses = {nd.output: use_count.get(nd.output, 0) - internal.get(nd.output, 0) for nd in chunk}
                compiled = _try_compile_subgraph(chunk, tensors, ext_uses, cols=cols, act_bytes=act_bytes,
                                                 model_bytes=model_bytes, max_ops=max_ops)
                if compiled is not None:
                    best_end, best_compiled = j, compiled
            if best_compiled is not None:
                pkg, sub_model = best_compiled
                chunk = nodes[i:best_end]
                in_name, out_name = sub_model['input']['name'], sub_model['output']
                in_h, in_w, in_c = tensors[in_name].shape
                out_h, out_w, out_c = tensors[out_name].shape
                steps.append(ProgramGroup(
                    program_id=ctx.new_id(),
                    layer_name=f'{chunk[0].name}..{chunk[-1].name}' if len(chunk) > 1 else chunk[0].name,
                    package=pkg, sub_model=sub_model,
                    calls=[TileCall(src_name=in_name, dst_name=out_name,
                                    src_y0=0, src_y1=in_h, src_x0=0, src_x1=in_w, src_c0=0, src_c1=in_c,
                                    dst_y0=0, dst_y1=out_h, dst_x0=0, dst_x1=out_w, dst_c0=0, dst_c1=out_c,
                                    valid_oh=out_h, valid_ow=out_w)],
                    is_fused_subgraph=len(chunk) > 1, oc_slice=(0, out_c)))
                i = best_end
                continue

        # 2) Per-layer tiling with absorbed single-consumer Requant / ReLU (/ MaxPool).
        nd = nodes[i]
        if nd.op == 'CPUFallback' or nd.backend == 'cpu_fallback':
            td = tensors[nd.output]
            steps.append(HostPostOp(op='CPUFallback', inputs=list(nd.inputs), output=nd.output,
                                    attrs=dict(nd.attrs), out_shape=td.shape, out_dtype=td.dtype))
            i += 1
            continue
        post_req = post_relu = pool = None
        j = i + 1
        if nd.op in ('Conv', 'FC', 'AvgPool', 'Add') and _single_consumer(nodes, j, nd.output, use_count, 'Requant'):
            post_req = nodes[j]
            j += 1
        cur = (post_req or nd).output
        relu_ok = post_req is None or post_req.attrs.get('clamp', [-8, 7])[1] >= 0
        if nd.op in ('Conv', 'FC', 'AvgPool', 'MaxPool', 'Add') and relu_ok and \
                _single_consumer(nodes, j, cur, use_count, 'ReLU'):
            post_relu = nodes[j]
            j += 1
            cur = post_relu.output
        if nd.op == 'Conv' and enable_fusion and _single_consumer(nodes, j, cur, use_count, 'MaxPool'):
            pool = nodes[j]

        if nd.op == 'Conv':
            plain = _tile_conv_layer(nd, post_req, post_relu, tensors, ctx)
            if pool is not None:
                try:
                    fused = _tile_conv_pool_fused(nd, post_req, post_relu, pool, tensors, ctx)
                except ValueError:
                    fused = None
                unfused = plain + _tile_pool_layer(pool, None, None, tensors, ctx)
                if fused is not None and _steps_cost(fused) < _steps_cost(unfused):
                    steps.extend(fused)
                    j += 1
                else:
                    steps.extend(plain)
            else:
                steps.extend(plain)
        elif nd.op == 'FC':
            steps.extend(_tile_fc_layer(nd, post_req, post_relu, tensors, ctx))
        elif nd.op in ('MaxPool', 'AvgPool'):
            steps.extend(_tile_pool_layer(nd, post_req, post_relu, tensors, ctx))
        elif nd.op == 'Add':
            hw = _tile_add_layer(nd, post_req, post_relu, tensors, ctx) if hardware_add else None
            if hw is None:  # non-4-bit inputs or wide weights: exact host fallback
                steps.append(HostPostOp(
                    op='Add', inputs=list(nd.inputs), output=nd.output, attrs=dict(nd.attrs),
                    out_shape=tensors[nd.output].shape, out_dtype=tensors[nd.output].dtype))
                steps.extend(_host_post_ops(post_req, post_relu, nd.output, tensors))
            else:
                steps.extend(hw)
        elif nd.op in ('Requant', 'ReLU'):
            td = tensors[nd.output]
            steps.append(HostPostOp(op=nd.op, inputs=list(nd.inputs), output=nd.output,
                                    attrs=dict(nd.attrs), out_shape=td.shape, out_dtype=td.dtype))
            j = i + 1
        else:
            raise ValueError(f'Unsupported IR op in tiler: {nd.op!r}')
        i = j
    # Rejected candidate tilings consumed ids; renumber so ids are dense and deterministic.
    for idx, group in enumerate(s for s in steps if isinstance(s, ProgramGroup)):
        group.program_id = f'p{idx:04d}'
    return TiledPlan(graph=graph, steps=steps, tensor_descs=tensors, metadata=dict(graph.metadata))
