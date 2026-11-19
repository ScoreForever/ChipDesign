"""Weight-Stationary 3D Tiling + Small-Layer Subgraph Fusion Planner for ABI 5.0."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
import math
from pathlib import Path
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
                    'calls': [asdict(c) for c in step.calls],
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


def _pack_conv_model(in_shape, in_dtype, oc, kh, kw, sh, sw, groups, weights, bias,
                     post_requant: dict | None, post_relu: bool) -> dict:
    nodes = [dict(
        op='Conv', input='x', output='conv', channels=oc, kernel=[kh, kw], stride=[sh, sw],
        pads=[0, 0, 0, 0], groups=groups, weights=weights, bias=bias,
    )]
    cur = 'conv'
    if post_requant is not None:
        nodes.append(dict(
            op='Requant', input=cur, output='req',
            multiplier=post_requant['multiplier'], shift=post_requant['shift'],
            clamp=post_requant['clamp'], dtype=post_requant['dtype'],
        ))
        cur = 'req'
    if post_relu:
        nodes.append(dict(op='ReLU', input=cur, output='relu'))
        cur = 'relu'
    return {
        'input': {'name': 'x', 'shape': list(in_shape), 'dtype': in_dtype},
        'nodes': nodes,
        'output': cur,
    }


def _estimate_conv_model_bytes(kh: int, kw: int, ic_tile: int, oc_tile: int, depthwise: bool,
                               cols: int = 8) -> int:
    """Exact ABI 5.0 model image size in bytes for a single Conv (+ optional fused Requant/ReLU)."""
    desc_bytes = 64
    if depthwise:
        taps = kh * kw
        weight_nibbles = oc_tile * taps
    else:
        k = kh * kw * ic_tile
        row_pad = (ic_tile == 1 and kw <= 8 and kh > 1)
        k_padded = kh * 8 if row_pad else abi.align(k, 8)
        weight_nibbles = 0
        for o_base in range(0, oc_tile, cols):
            width = 4 if cols == 8 and oc_tile - o_base <= 4 else cols
            weight_nibbles += k_padded * width
    weight_bytes = abi.align((weight_nibbles + 1) // 2, 4)
    param_bytes = oc_tile * 4
    return desc_bytes + weight_bytes + param_bytes


def _tensor_bytes(h: int, w: int, c: int, dtype: str) -> int:
    n = h * w * c
    return n * 4 if dtype == 'i32' else (n + 1) // 2


def _choose_conv_tiling(h: int, w: int, ic: int, oc: int, kh: int, kw: int, sh: int, sw: int,
                        oh: int, ow: int, depthwise: bool, in_dtype: str, out_dtype_single_k: str,
                        *, cols: int, act_bytes: int, model_bytes: int) -> tuple[int, int, int, int, bool]:
    """Choose weight-stationary (ic_tile, oc_tile, oh_tile, ow_tile, k_tiled) within SRAM limits."""
    total_sram = act_bytes + model_bytes  # 16384 B

    # 1) Determine largest legal ic_tile (k <= MAX_K and weights fit with room for activations)
    if depthwise:
        ic_tile = ic  # depthwise tiles channels via oc_tile == ic_tile
        k_tiled = False
    else:
        max_ic_k = MAX_K // (kh * kw)
        if max_ic_k < 1:
            raise ValueError(f'Kernel {kh}x{kw} cannot fit MAX_K={MAX_K}')
        ic_tile = min(ic, max_ic_k)
        if ic_tile < ic and ic_tile >= 8:
            ic_tile = (ic_tile // 8) * 8
        # Ensure even a minimal oc_tile fits in model_bytes with >= 2048 B left for activations
        min_oc = min(oc, cols)
        while ic_tile > 1 and _estimate_conv_model_bytes(kh, kw, ic_tile, min_oc, False, cols) > min(model_bytes, total_sram - 2048):
            ic_tile = max(1, ic_tile // 2)
        k_tiled = ic_tile < ic

    tile_out_dtype = 'i32' if k_tiled else out_dtype_single_k

    # 2) Search (oc_tile, oh_tile, ow_tile) balancing weight reuse vs activation SRAM!
    # Prefer large oc_tile (fewer MODEL_COMMITs) while keeping enough activation SRAM for >= reasonable spatial tile.
    best = None  # tuple: (score_jtag_bytes, oc_tile, oh_tile, ow_tile)
    oc_step = cols if (depthwise or oc >= cols) else 1
    oc_candidates = []
    if depthwise:
        max_oc = min(oc, MAX_OC)
        oc_candidates = [c for c in range(cols, max_oc + 1, cols)]
    else:
        max_oc = min(oc, MAX_OC)
        for c_cand in range(oc_step, max_oc + 1, oc_step):
            oc_candidates.append(c_cand)
        if max_oc not in oc_candidates:
            oc_candidates.append(max_oc)

    n_k_tiles = math.ceil(ic / ic_tile) if not depthwise else 1

    for oc_t in reversed(oc_candidates):
        c_in_t = oc_t if depthwise else ic_tile
        m_bytes = _estimate_conv_model_bytes(kh, kw, c_in_t, oc_t, depthwise, cols)
        if m_bytes > model_bytes:
            continue
        usable_act = total_sram - m_bytes
        if usable_act < 64:
            continue

        # Find largest (oh_t, ow_t) fitting usable_act
        # Fast search: try full width first, then halving/divisor candidates
        best_hw = None
        ow_cands = sorted(set([ow, min(ow, 64), min(ow, 32), min(ow, 16), min(ow, 14), min(ow, 8), min(ow, 7), min(ow, 4), 2, 1]), reverse=True)
        for ow_t in ow_cands:
            if ow_t <= 0 or ow_t > MAX_SPATIAL:
                continue
            iw_t = (ow_t - 1) * sw + kw
            if iw_t > MAX_SPATIAL:
                continue
            # Binary search max oh_t in [1..min(oh, MAX_SPATIAL)]
            lo_h, hi_h, ok_h = 1, min(oh, MAX_SPATIAL), 0
            while lo_h <= hi_h:
                mid = (lo_h + hi_h) // 2
                ih_t = (mid - 1) * sh + kh
                if ih_t > MAX_SPATIAL:
                    hi_h = mid - 1
                    continue
                in_b = abi.align(_tensor_bytes(ih_t, iw_t, c_in_t, in_dtype), 4)
                out_b = abi.align(_tensor_bytes(mid, ow_t, oc_t, tile_out_dtype), 4)
                if in_b + out_b <= usable_act:
                    ok_h = mid
                    lo_h = mid + 1
                else:
                    hi_h = mid - 1
            if ok_h > 0:
                tiles_cnt = math.ceil(oh / ok_h) * math.ceil(ow / ow_t)
                ih_ok = (ok_h - 1) * sh + kh
                in_b = _tensor_bytes(ih_ok, iw_t, c_in_t, in_dtype)
                out_b = _tensor_bytes(ok_h, ow_t, oc_t, tile_out_dtype)
                cand_hw = (tiles_cnt * (in_b + out_b) + tiles_cnt * 64, ok_h, ow_t, tiles_cnt, in_b, out_b)
                if best_hw is None or cand_hw[0] < best_hw[0]:
                    best_hw = cand_hw
        if best_hw is None:
            continue
        _, oh_t, ow_t, tiles_cnt, in_b, out_b = best_hw
        n_oc_tiles = math.ceil(oc / oc_t)
        est_total_jtag = n_oc_tiles * n_k_tiles * (m_bytes + tiles_cnt * (in_b + out_b + 64))
        if best is None or est_total_jtag < best[0]:
            best = (est_total_jtag, oc_t, oh_t, ow_t)

    if best is None:
        raise ValueError(f'Unable to tile Conv ({h}x{w}x{ic} -> oc={oc}, k={kh}x{kw}) into ABI 5.0 SRAM')
    _, oc_t, oh_t, ow_t = best
    return (oc_t if depthwise else ic_tile), oc_t, oh_t, ow_t, k_tiled


def _tile_conv_layer(conv_node: NodeIR, post_req: NodeIR | None, post_relu: NodeIR | None,
                     tensors: dict[str, TensorDesc], *, cols: int, act_bytes: int, model_bytes: int,
                     max_ops: int, prog_counter: list[int]) -> list[ProgramGroup | HostPostOp]:
    src = conv_node.inputs[0]
    t_in = tensors[src]
    h, w, ic = t_in.shape
    a = conv_node.attrs
    oc = int(a['channels'])
    kh, kw = abi.pair(a.get('kernel', 3), 'kernel')
    sh, sw = abi.pair(a.get('stride', 1), 'stride')
    pt, pl, pb, pr = [int(v) for v in a.get('pads', [0, 0, 0, 0])]
    groups = int(a.get('groups', 1))
    depthwise = groups != 1
    oh = (h + pt + pb - kh) // sh + 1
    ow = (w + pl + pr - kw) // sw + 1

    final_node = post_relu or post_req or conv_node
    final_out_name = final_node.output
    final_out_dtype = tensors[final_out_name].dtype

    w_full = np.asarray(a['weights'], dtype=np.int64)
    if depthwise:
        w_full = w_full.reshape(oc, kh, kw)
    else:
        w_full = w_full.reshape(oc, kh, kw, ic)
    b_full = np.asarray(a.get('bias', [0] * oc), dtype=np.int64)

    ic_t, oc_t, oh_t, ow_t, k_tiled = _choose_conv_tiling(
        h, w, ic, oc, kh, kw, sh, sw, oh, ow, depthwise, t_in.dtype, final_out_dtype,
        cols=cols, act_bytes=act_bytes, model_bytes=model_bytes,
    )

    ih_u = (oh_t - 1) * sh + kh
    iw_u = (ow_t - 1) * sw + kw
    steps: list[ProgramGroup | HostPostOp] = []
    accum_dst = conv_node.output if k_tiled else final_out_name

    # Weight-Stationary 3D Loop: outer (oc_tile, k_tile) -> 1 MODEL_COMMIT; inner (oh_tile, ow_tile) -> INFER!
    for oc0 in range(0, oc, oc_t):
        oc1 = min(oc, oc0 + oc_t)
        oc_cur = oc1 - oc0
        ic_ranges = [(oc0, oc1)] if depthwise else [(k0, min(ic, k0 + ic_t)) for k0 in range(0, ic, ic_t)]

        for k_idx, (ic0, ic1) in enumerate(ic_ranges):
            ic_cur = ic1 - ic0
            if depthwise:
                w_sub = w_full[oc0:oc1].reshape(-1).tolist()
            else:
                w_sub = w_full[oc0:oc1, :, :, ic0:ic1].reshape(-1).tolist()
            b_sub = b_full[oc0:oc1].tolist() if k_idx == 0 else [0] * oc_cur

            req_sub = None
            relu_sub = False
            if not k_tiled:
                if post_req is not None:
                    ra = post_req.attrs
                    mm = list(ra['multiplier'])
                    ss = list(ra['shift'])
                    if len(mm) == 1:
                        mm = mm * oc
                    if len(ss) == 1:
                        ss = ss * oc
                    req_sub = dict(
                        multiplier=mm[oc0:oc1], shift=ss[oc0:oc1],
                        clamp=list(ra.get('clamp', [0, 15] if ra.get('dtype') == 'u4' else [-8, 7])),
                        dtype=ra.get('dtype', 'i4'),
                    )
                if post_relu is not None:
                    relu_sub = True

            sub_model = _pack_conv_model(
                (ih_u, iw_u, ic_cur), t_in.dtype, oc_cur, kh, kw, sh, sw,
                oc_cur if depthwise else 1, w_sub, b_sub, req_sub, relu_sub,
            )
            pkg = abi.compile_model(sub_model, fuse=True, cols=cols, act_bytes=act_bytes,
                                    model_bytes=model_bytes, max_ops=max_ops)
            calls: list[TileCall] = []
            for oy0 in range(0, oh, oh_t):
                oy1 = min(oh, oy0 + oh_t)
                iy0 = oy0 * sh - pt
                iy1 = iy0 + ih_u
                for ox0 in range(0, ow, ow_t):
                    ox1 = min(ow, ox0 + ow_t)
                    ix0 = ox0 * sw - pl
                    ix1 = ix0 + iw_u
                    calls.append(TileCall(
                        src_name=src, dst_name=accum_dst,
                        src_y0=iy0, src_y1=iy1, src_x0=ix0, src_x1=ix1, src_c0=ic0, src_c1=ic1,
                        dst_y0=oy0, dst_y1=oy1, dst_x0=ox0, dst_x1=ox1, dst_c0=oc0, dst_c1=oc1,
                        valid_oh=oy1 - oy0, valid_ow=ox1 - ox0,
                        pad_value=0,
                        accum_mode='add_i32' if (k_tiled and k_idx > 0) else 'assign',
                    ))
            pid = f'p{prog_counter[0]:04d}'
            prog_counter[0] += 1
            steps.append(ProgramGroup(
                program_id=pid, layer_name=conv_node.name, package=pkg, sub_model=sub_model,
                calls=calls, is_fused_subgraph=False, oc_slice=(oc0, oc1), k_slice=(ic0, ic1),
            ))

    if k_tiled:
        if post_req is not None:
            td = tensors[post_req.output]
            steps.append(HostPostOp(
                op='Requant', inputs=[conv_node.output], output=post_req.output,
                attrs=dict(post_req.attrs), out_shape=td.shape, out_dtype=td.dtype,
            ))
        if post_relu is not None:
            td = tensors[post_relu.output]
            steps.append(HostPostOp(
                op='ReLU', inputs=[post_relu.inputs[0]], output=post_relu.output,
                attrs={}, out_shape=td.shape, out_dtype=td.dtype,
            ))
    return steps


def _tile_fc_layer(fc_node: NodeIR, post_req: NodeIR | None, post_relu: NodeIR | None,
                   tensors: dict[str, TensorDesc], *, cols: int, act_bytes: int, model_bytes: int,
                   max_ops: int, prog_counter: list[int]) -> list[ProgramGroup | HostPostOp]:
    src = fc_node.inputs[0]
    t_in = tensors[src]
    k_in = math.prod(t_in.shape)
    oc = int(fc_node.attrs['channels'])
    w_full = np.asarray(fc_node.attrs['weights'], dtype=np.int64).reshape(oc, k_in)
    b_full = np.asarray(fc_node.attrs.get('bias', [0] * oc), dtype=np.int64)
    final_node = post_relu or post_req or fc_node
    final_out_name = final_node.output
    final_out_dtype = tensors[final_out_name].dtype

    total_sram = act_bytes + model_bytes
    k_t = min(k_in, MAX_K)
    if k_t < k_in and k_t >= 8:
        k_t = (k_t // 8) * 8
    while k_t > 1 and _estimate_conv_model_bytes(1, 1, k_t, min(oc, cols), False, cols) > min(model_bytes, total_sram - 1024):
        k_t = max(1, k_t // 2)
    k_tiled = k_t < k_in
    out_dt = 'i32' if k_tiled else final_out_dtype

    # Largest oc_t <= min(oc, MAX_OC) fitting model + activation SRAM
    oc_t = min(oc, MAX_OC)
    while oc_t > 1:
        mb = _estimate_conv_model_bytes(1, 1, k_t, oc_t, False, cols)
        in_b = abi.align(_tensor_bytes(1, 1, k_t, t_in.dtype), 4)
        out_b = abi.align(_tensor_bytes(1, 1, oc_t, out_dt), 4)
        if mb <= model_bytes and in_b + out_b <= total_sram - mb:
            break
        oc_t = (oc_t - 1) if oc_t <= cols else max(cols, ((oc_t - 1) // cols) * cols)

    steps: list[ProgramGroup | HostPostOp] = []
    accum_dst = fc_node.output if k_tiled else final_out_name
    for oc0 in range(0, oc, oc_t):
        oc1 = min(oc, oc0 + oc_t)
        oc_cur = oc1 - oc0
        for k_idx, k0 in enumerate(range(0, k_in, k_t)):
            k1 = min(k_in, k0 + k_t)
            k_cur = k1 - k0
            w_sub = w_full[oc0:oc1, k0:k1].reshape(-1).tolist()
            b_sub = b_full[oc0:oc1].tolist() if k_idx == 0 else [0] * oc_cur
            nodes = [dict(op='FC', input='x', output='fc', channels=oc_cur, weights=w_sub, bias=b_sub)]
            cur = 'fc'
            if not k_tiled and post_req is not None:
                ra = post_req.attrs
                mm = list(ra['multiplier'])
                ss = list(ra['shift'])
                if len(mm) == 1:
                    mm = mm * oc
                if len(ss) == 1:
                    ss = ss * oc
                nodes.append(dict(op='Requant', input=cur, output='req',
                                  multiplier=mm[oc0:oc1], shift=ss[oc0:oc1],
                                  clamp=list(ra.get('clamp', [0, 15] if ra.get('dtype') == 'u4' else [-8, 7])),
                                  dtype=ra.get('dtype', 'i4')))
                cur = 'req'
            if not k_tiled and post_relu is not None:
                nodes.append(dict(op='ReLU', input=cur, output='relu'))
                cur = 'relu'
            sub_model = {'input': {'name': 'x', 'shape': [1, 1, k_cur], 'dtype': t_in.dtype},
                         'nodes': nodes, 'output': cur}
            pkg = abi.compile_model(sub_model, fuse=True, cols=cols, act_bytes=act_bytes,
                                    model_bytes=model_bytes, max_ops=max_ops)
            call = TileCall(
                src_name=src, dst_name=accum_dst,
                src_y0=0, src_y1=1, src_x0=0, src_x1=1, src_c0=k0, src_c1=k1,
                dst_y0=0, dst_y1=1, dst_x0=0, dst_x1=1, dst_c0=oc0, dst_c1=oc1,
                valid_oh=1, valid_ow=1, pad_value=0,
                accum_mode='add_i32' if (k_tiled and k_idx > 0) else 'assign',
                flatten_input=True,
            )
            pid = f'p{prog_counter[0]:04d}'
            prog_counter[0] += 1
            steps.append(ProgramGroup(
                program_id=pid, layer_name=fc_node.name, package=pkg, sub_model=sub_model,
                calls=[call], is_fused_subgraph=False, oc_slice=(oc0, oc1), k_slice=(k0, k1),
            ))

    if k_tiled:
        if post_req is not None:
            td = tensors[post_req.output]
            steps.append(HostPostOp(op='Requant', inputs=[fc_node.output], output=post_req.output,
                                    attrs=dict(post_req.attrs), out_shape=td.shape, out_dtype=td.dtype))
        if post_relu is not None:
            td = tensors[post_relu.output]
            steps.append(HostPostOp(op='ReLU', inputs=[post_relu.inputs[0]], output=post_relu.output,
                                    attrs={}, out_shape=td.shape, out_dtype=td.dtype))
    return steps


def _tile_pool_layer(pool_node: NodeIR, post_req: NodeIR | None, post_relu: NodeIR | None,
                     tensors: dict[str, TensorDesc], *, cols: int, act_bytes: int, model_bytes: int,
                     max_ops: int, prog_counter: list[int]) -> list[ProgramGroup | HostPostOp]:
    """Tile standalone MaxPool / AvgPool (including non-zero padded MaxPool via host tile padding)."""
    src = pool_node.inputs[0]
    t_in = tensors[src]
    h, w, c = t_in.shape
    kh, kw = abi.pair(pool_node.attrs.get('kernel', 2), 'kernel')
    sh, sw = abi.pair(pool_node.attrs.get('stride', [kh, kw]), 'stride')
    pt, pl, pb, pr = [int(v) for v in pool_node.attrs.get('pads', [0, 0, 0, 0])]
    oh = (h + pt + pb - kh) // sh + 1
    ow = (w + pl + pr - kw) // sw + 1
    final_node = post_relu or post_req or pool_node
    out_name = final_node.output
    out_dtype = tensors[out_name].dtype

    total_sram = act_bytes + model_bytes
    pad_val = pad_value_for_dtype(t_in.dtype, pool_node.op)

    # Choose channel tile c_t <= min(c, MAX_OC) and spatial tile (oh_t, ow_t)
    c_t = min(c, MAX_OC)
    best = None
    while c_t >= 1:
        mb = 64 + (c_t * 4 if post_req is not None else 0)
        usable = total_sram - mb
        for ow_t in sorted(set([ow, min(ow, 32), min(ow, 16), min(ow, 8), 4, 2, 1]), reverse=True):
            iw_t = (ow_t - 1) * sw + kw
            if iw_t > MAX_SPATIAL or ow_t > MAX_SPATIAL:
                continue
            lo_h, hi_h, ok_h = 1, min(oh, MAX_SPATIAL), 0
            while lo_h <= hi_h:
                mid = (lo_h + hi_h) // 2
                ih_t = (mid - 1) * sh + kh
                if ih_t > MAX_SPATIAL:
                    hi_h = mid - 1
                    continue
                in_b = abi.align(_tensor_bytes(ih_t, iw_t, c_t, t_in.dtype), 4)
                out_b = abi.align(_tensor_bytes(mid, ow_t, c_t, out_dtype), 4)
                if in_b + out_b <= usable:
                    ok_h = mid
                    lo_h = mid + 1
                else:
                    hi_h = mid - 1
            if ok_h > 0:
                best = (c_t, ok_h, ow_t)
                break
        if best is not None:
            break
        c_t = max(1, c_t // 2)

    if best is None:
        raise ValueError(f'Unable to tile {pool_node.op} ({h}x{w}x{c}, k={kh}x{kw}) into ABI 5.0 SRAM')
    c_t, oh_t, ow_t = best
    ih_u = (oh_t - 1) * sh + kh
    iw_u = (ow_t - 1) * sw + kw
    steps: list[ProgramGroup | HostPostOp] = []

    for c0 in range(0, c, c_t):
        c1 = min(c, c0 + c_t)
        c_cur = c1 - c0
        nodes = [dict(op=pool_node.op, input='x', output='p', kernel=[kh, kw], stride=[sh, sw], pads=[0, 0, 0, 0])]
        cur = 'p'
        if post_req is not None:
            ra = post_req.attrs
            mm = list(ra['multiplier'])
            ss = list(ra['shift'])
            if len(mm) == 1:
                mm = mm * c
            if len(ss) == 1:
                ss = ss * c
            nodes.append(dict(op='Requant', input=cur, output='req',
                              multiplier=mm[c0:c1], shift=ss[c0:c1],
                              clamp=list(ra.get('clamp', [0, 15] if ra.get('dtype') == 'u4' else [-8, 7])),
                              dtype=ra.get('dtype', 'i4')))
            cur = 'req'
        if post_relu is not None:
            nodes.append(dict(op='ReLU', input=cur, output='relu'))
            cur = 'relu'
        sub_model = {'input': {'name': 'x', 'shape': [ih_u, iw_u, c_cur], 'dtype': t_in.dtype},
                     'nodes': nodes, 'output': cur}
        pkg = abi.compile_model(sub_model, fuse=True, cols=cols, act_bytes=act_bytes,
                                model_bytes=model_bytes, max_ops=max_ops)
        calls: list[TileCall] = []
        for oy0 in range(0, oh, oh_t):
            oy1 = min(oh, oy0 + oh_t)
            iy0 = oy0 * sh - pt
            iy1 = iy0 + ih_u
            for ox0 in range(0, ow, ow_t):
                ox1 = min(ow, ox0 + ow_t)
                ix0 = ox0 * sw - pl
                ix1 = ix0 + iw_u
                calls.append(TileCall(
                    src_name=src, dst_name=out_name,
                    src_y0=iy0, src_y1=iy1, src_x0=ix0, src_x1=ix1, src_c0=c0, src_c1=c1,
                    dst_y0=oy0, dst_y1=oy1, dst_x0=ox0, dst_x1=ox1, dst_c0=c0, dst_c1=c1,
                    valid_oh=oy1 - oy0, valid_ow=ox1 - ox0, pad_value=pad_val, accum_mode='assign',
                ))
        pid = f'p{prog_counter[0]:04d}'
        prog_counter[0] += 1
        steps.append(ProgramGroup(
            program_id=pid, layer_name=pool_node.name, package=pkg, sub_model=sub_model,
            calls=calls, is_fused_subgraph=False, oc_slice=(c0, c1),
        ))
    return steps


def compile_graph_to_plan(graph: GraphIR, *, cols: int = 8, act_bytes: int = 8192,
                          model_bytes: int = 8192, max_ops: int = 128,
                          enable_fusion: bool = True) -> TiledPlan:
    """Compile any GraphIR into a weight-stationary 3D TiledPlan for ABI 5.0."""
    tensors = infer_tensors(graph)
    nodes = graph.nodes
    n_total = len(nodes)
    # Count total consumer references of each tensor (plus 1 for graph.output)
    use_count: dict[str, int] = {graph.output: 1}
    for n in nodes:
        for inp in n.inputs:
            use_count[inp] = use_count.get(inp, 0) + 1

    steps: list[ProgramGroup | HostPostOp] = []
    prog_counter = [0]
    i = 0

    while i < n_total:
        # 1) Try greedy multi-layer subgraph fusion when enabled
        if enable_fusion and nodes[i].backend == 'npu' and nodes[i].op != 'CPUFallback':
            best_end = -1
            best_compiled = None
            # Try extending end j as far as possible within SRAM & op limits
            for j in range(i + 1, min(n_total, i + max_ops) + 1):
                chunk = nodes[i:j]
                if chunk[-1].backend != 'npu' or chunk[-1].op == 'CPUFallback':
                    break
                # Compute external consumer counts for outputs produced inside chunk
                internal_uses: dict[str, int] = {}
                for nd in chunk:
                    for inp in nd.inputs:
                        internal_uses[inp] = internal_uses.get(inp, 0) + 1
                ext_uses = {nd.output: use_count.get(nd.output, 0) - internal_uses.get(nd.output, 0) for nd in chunk}
                compiled = _try_compile_subgraph(
                    chunk, tensors, ext_uses,
                    cols=cols, act_bytes=act_bytes, model_bytes=model_bytes, max_ops=max_ops,
                )
                if compiled is not None:
                    best_end = j
                    best_compiled = compiled
            if best_compiled is not None:
                pkg, sub_model = best_compiled
                chunk = nodes[i:best_end]
                in_name = sub_model['input']['name']
                out_name = sub_model['output']
                in_h, in_w, in_c = tensors[in_name].shape
                out_h, out_w, out_c = tensors[out_name].shape
                pid = f'p{prog_counter[0]:04d}'
                prog_counter[0] += 1
                steps.append(ProgramGroup(
                    program_id=pid,
                    layer_name=f'{chunk[0].name}..{chunk[-1].name}' if len(chunk) > 1 else chunk[0].name,
                    package=pkg, sub_model=sub_model,
                    calls=[TileCall(
                        src_name=in_name, dst_name=out_name,
                        src_y0=0, src_y1=in_h, src_x0=0, src_x1=in_w, src_c0=0, src_c1=in_c,
                        dst_y0=0, dst_y1=out_h, dst_x0=0, dst_x1=out_w, dst_c0=0, dst_c1=out_c,
                        valid_oh=out_h, valid_ow=out_w, pad_value=0, accum_mode='assign',
                    )],
                    is_fused_subgraph=len(chunk) > 1,
                    oc_slice=(0, out_c),
                ))
                i = best_end
                continue

        # 2) Per-layer fallback (weight-stationary 3D tiling or exact host op)
        nd = nodes[i]
        if nd.op == 'CPUFallback' or nd.backend == 'cpu_fallback':
            td = tensors[nd.output]
            steps.append(HostPostOp(op='CPUFallback', inputs=list(nd.inputs), output=nd.output,
                                    attrs=dict(nd.attrs), out_shape=td.shape, out_dtype=td.dtype))
            i += 1
            continue

        # Attach immediately following single-consumer Requant / ReLU to Conv / FC / Pool
        post_req = None
        post_relu = None
        adv = 1
        if nd.op in ('Conv', 'FC', 'AvgPool') and i + adv < n_total:
            nxt = nodes[i + adv]
            if nxt.op == 'Requant' and nxt.inputs == [nd.output] and use_count.get(nd.output, 0) == 1:
                post_req = nxt
                adv += 1
        prev_out = (post_req or nd).output
        if nd.op in ('Conv', 'FC', 'AvgPool', 'MaxPool') and i + adv < n_total:
            nxt = nodes[i + adv]
            if nxt.op == 'ReLU' and nxt.inputs == [prev_out] and use_count.get(prev_out, 0) == 1:
                post_relu = nxt
                adv += 1

        if nd.op == 'Conv':
            steps.extend(_tile_conv_layer(
                nd, post_req, post_relu, tensors,
                cols=cols, act_bytes=act_bytes, model_bytes=model_bytes,
                max_ops=max_ops, prog_counter=prog_counter,
            ))
            i += adv
        elif nd.op == 'FC':
            steps.extend(_tile_fc_layer(
                nd, post_req, post_relu, tensors,
                cols=cols, act_bytes=act_bytes, model_bytes=model_bytes,
                max_ops=max_ops, prog_counter=prog_counter,
            ))
            i += adv
        elif nd.op in ('MaxPool', 'AvgPool'):
            steps.extend(_tile_pool_layer(
                nd, post_req, post_relu, tensors,
                cols=cols, act_bytes=act_bytes, model_bytes=model_bytes,
                max_ops=max_ops, prog_counter=prog_counter,
            ))
            i += adv
        elif nd.op in ('Add', 'Requant', 'ReLU'):
            # Standalone elementwise op between tiled large layers executes with exact ABI integer math
            td = tensors[nd.output]
            steps.append(HostPostOp(op=nd.op, inputs=list(nd.inputs), output=nd.output,
                                    attrs=dict(nd.attrs), out_shape=td.shape, out_dtype=td.dtype))
            i += 1
        else:
            raise ValueError(f'Unsupported IR op in tiler: {nd.op!r}')

    return TiledPlan(graph=graph, steps=steps, tensor_descs=tensors, metadata=dict(graph.metadata))
