"""Generic ONNX -> ABI 5.0 GraphIR frontend (opset 13-19, zero network hardcoding)."""
from __future__ import annotations

import math
from pathlib import Path
import sys
import numpy as np
import onnx
from onnx import numpy_helper, shape_inference

_HOST_DIR = str(Path(__file__).resolve().parents[1] / 'host')
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)

import npu_program as abi  # noqa: E402
from .ir import GraphIR, NodeIR, TensorDesc  # noqa: E402

BIAS_MIN = -(1 << (abi.BIAS_BITS - 1))
BIAS_MAX = (1 << (abi.BIAS_BITS - 1)) - 1


def float_scale_to_q15(scale: float) -> tuple[int, int]:
    """Convert a positive float multiplier into hardware Q15 (multiplier, shift)."""
    if not math.isfinite(scale) or scale <= 0.0:
        return 0, 0
    frac, exp = math.frexp(scale)
    q = int(round(frac * (1 << 15)))
    if q == (1 << 15):
        q //= 2
        exp += 1
    if exp < -31:
        return 0, 0
    if exp > 31:
        return (1 << 15) - 1, 31
    return q, exp


def _get_attr(node, name: str, default=None):
    for attr in node.attribute:
        if attr.name == name:
            if attr.type == onnx.AttributeProto.INT:
                return int(attr.i)
            if attr.type == onnx.AttributeProto.FLOAT:
                return float(attr.f)
            if attr.type == onnx.AttributeProto.INTS:
                return [int(v) for v in attr.ints]
            if attr.type == onnx.AttributeProto.FLOATS:
                return [float(v) for v in attr.floats]
            if attr.type == onnx.AttributeProto.STRING:
                return attr.s.decode('utf-8')
            if attr.type == onnx.AttributeProto.TENSOR:
                return numpy_helper.to_array(attr.t)
    return default


def _extract_shapes(model: onnx.ModelProto) -> dict[str, tuple[int, ...]]:
    """Collect tensor shapes (NCHW or NC) after ONNX shape inference."""
    inferred = shape_inference.infer_shapes(model)
    shapes: dict[str, tuple[int, ...]] = {}
    for vi in list(inferred.graph.input) + list(inferred.graph.value_info) + list(inferred.graph.output):
        tt = vi.type.tensor_type
        if tt.HasField('shape'):
            dims = []
            for d in tt.shape.dim:
                if d.HasField('dim_value') and d.dim_value > 0:
                    dims.append(int(d.dim_value))
                else:
                    dims.append(-1)
            shapes[vi.name] = tuple(dims)
    for init in inferred.graph.initializer:
        shapes[init.name] = tuple(int(v) for v in init.dims)
    return shapes


def _fold_conv_bn(w: np.ndarray, b: np.ndarray | None, gamma: np.ndarray, beta: np.ndarray,
                  mean: np.ndarray, var: np.ndarray, eps: float) -> tuple[np.ndarray, np.ndarray]:
    """Analytically fold BatchNormalization into Conv weights (OC,IC,KH,KW) and bias (OC)."""
    oc = w.shape[0]
    b_arr = np.zeros(oc, dtype=np.float64) if b is None else b.astype(np.float64)
    inv_std = gamma.astype(np.float64) / np.sqrt(var.astype(np.float64) + eps)
    w_fold = w.astype(np.float64) * inv_std.reshape(oc, 1, 1, 1)
    b_fold = (b_arr - mean.astype(np.float64)) * inv_std + beta.astype(np.float64)
    return w_fold, b_fold


def _quantize_weights_per_channel(w_fp: np.ndarray, explicit_scales=None, *, global_scale: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Symmetric INT4 [-8, 7] weight quantization (per-channel, or single global scale for raw logits)."""
    oc = w_fp.shape[0]
    if explicit_scales is not None:
        w_scales = np.asarray(explicit_scales, dtype=np.float64).reshape(-1)
        if w_scales.size == 1:
            w_scales = np.broadcast_to(w_scales, (oc,)).copy()
    elif global_scale:
        max_abs = float(np.max(np.abs(w_fp)))
        sc = max_abs / 7.0 if max_abs > 1e-8 else 1.0
        w_scales = np.full((oc,), sc, dtype=np.float64)
    else:
        max_abs = np.max(np.abs(w_fp.reshape(oc, -1)), axis=1)
        w_scales = np.where(max_abs > 1e-8, max_abs / 7.0, 1.0)
    scale_view = w_scales.reshape((oc,) + (1,) * (w_fp.ndim - 1))
    w_q = np.clip(np.rint(w_fp / scale_view), -8, 7).astype(np.int64)
    return w_q, w_scales


def _clamp_bias_18(b_int: np.ndarray) -> list[int]:
    return np.clip(b_int.astype(np.int64), BIAS_MIN, BIAS_MAX).tolist()


def _resolve_auto_pad(node, h: int, w: int, kh: int, kw: int, sh: int, sw: int) -> list[int]:
    """Resolve ONNX auto_pad (NOTSET / VALID / SAME_UPPER / SAME_LOWER) into [pt, pl, pb, pr]."""
    auto_pad = _get_attr(node, 'auto_pad', 'NOTSET') or 'NOTSET'
    if auto_pad == 'NOTSET':
        return [int(v) for v in _get_attr(node, 'pads', [0, 0, 0, 0])]
    if auto_pad == 'VALID':
        return [0, 0, 0, 0]
    if auto_pad in ('SAME_UPPER', 'SAME_LOWER'):
        oh = math.ceil(h / sh)
        ow = math.ceil(w / sw)
        pad_h = max((oh - 1) * sh + kh - h, 0)
        pad_w = max((ow - 1) * sw + kw - w, 0)
        if auto_pad == 'SAME_UPPER':
            pt, pb = pad_h // 2, pad_h - (pad_h // 2)
            pl, pr = pad_w // 2, pad_w - (pad_w // 2)
        else:
            pb, pt = pad_h // 2, pad_h - (pad_h // 2)
            pr, pl = pad_w // 2, pad_w - (pad_w // 2)
        return [pt, pl, pb, pr]
    raise NotImplementedError(f'Unsupported ONNX auto_pad={auto_pad!r} at {node.name!r}')


def _best_add_weights(sa: float, sb: float, out_scale: float) -> tuple[int, int, int, int]:
    """Choose signed 8-bit integer (wa, wb) and Q15 (mult, shift) minimizing scale error."""
    # Target ratio wa/wb ≈ sa/sb, using large 7-bit precision (max 127) to preserve dynamic range.
    m = max(sa, sb, 1e-12)
    wa = max(1, min(127, int(round(127.0 * (sa / m)))))
    wb = max(1, min(127, int(round(127.0 * (sb / m)))))
    # Effective real value of (wa*a + wb*b) has scale ≈ (sa / wa)
    eff_scale = (sa / wa + sb / wb) * 0.5
    q_mult, q_shift = float_scale_to_q15(eff_scale / max(out_scale, 1e-12))
    return wa, wb, q_mult, q_shift


def load_onnx_to_ir(onnx_path_or_model: str | Path | onnx.ModelProto,
                    quant_config: dict | None = None) -> GraphIR:
    """Parse any standard ONNX graph into an ABI 5.0 quantized GraphIR.

    Supports:
      - Automatic Conv + BatchNormalization folding
      - QDQ (QuantizeLinear / DequantizeLinear) scale extraction OR explicit quant_config
      - Conv (standard + depthwise), Gemm/MatMul+Add (FC), MaxPool, AveragePool,
        GlobalAveragePool, Relu, Clip(0, x), Add, Flatten/Reshape/Squeeze
      - CPUFallback routing for tail ops such as Softmax
    """
    model = onnx.load(str(onnx_path_or_model)) if isinstance(onnx_path_or_model, (str, Path)) else onnx_path_or_model
    qcfg = dict(quant_config or {})
    inferred = shape_inference.infer_shapes(model)
    graph = inferred.graph
    shapes = _extract_shapes(inferred)
    inits = {t.name: numpy_helper.to_array(t) for t in graph.initializer}

    # Pre-scan QDQ (QuantizeLinear / DequantizeLinear) nodes:
    # - For constant weight initializers passed through Q/DQ, materialize true dequantized float tensors
    #   (applying per-tensor or per-channel scale and zero_point!) and capture explicit per-channel scales.
    # - For runtime activations, record activation scales/dtypes and transparent QDQ bypass edges.
    qdq_scales: dict[str, float] = {}
    qdq_dtypes: dict[str, str] = {}
    qdq_bypass: dict[str, str] = {}
    qdq_weight_scales: dict[str, np.ndarray] = {}
    for node in graph.node:
        if node.op_type in ('QuantizeLinear', 'DequantizeLinear'):
            src = node.input[0]
            dst = node.output[0]
            scale_arr = inits.get(node.input[1])
            zp_arr = inits.get(node.input[2]) if len(node.input) >= 3 and node.input[2] in inits else None
            axis = int(_get_attr(node, 'axis', 1))
            if src in inits and scale_arr is not None:
                arr = inits[src].astype(np.float64)
                sc_f = scale_arr.astype(np.float64)
                zp_f = zp_arr.astype(np.float64) if zp_arr is not None else np.zeros_like(sc_f)
                if sc_f.ndim == 1 and arr.ndim > 1:
                    eff_axis = axis if axis >= 0 else arr.ndim + axis
                    bshape = [1] * arr.ndim
                    bshape[eff_axis] = sc_f.size
                    sc_b = sc_f.reshape(bshape)
                    zp_b = zp_f.reshape(bshape) if zp_f.size == sc_f.size else zp_f
                else:
                    sc_b = sc_f
                    zp_b = zp_f
                if node.op_type == 'QuantizeLinear':
                    q_lo, q_hi = (0, 255) if (zp_arr is not None and zp_arr.dtype == np.uint8) else (-128, 127)
                    inits[dst] = np.clip(np.rint(arr / np.maximum(sc_b, 1e-12)) + zp_b, q_lo, q_hi)
                else:
                    inits[dst] = (arr - zp_b) * sc_b
                if sc_f.ndim <= 1:
                    qdq_weight_scales[dst] = sc_f.reshape(-1)
                continue
            if scale_arr is not None and scale_arr.size == 1:
                sc = float(scale_arr.reshape(-1)[0])
                qdq_scales[dst] = sc
                qdq_scales[src] = sc
            if zp_arr is not None:
                if np.any(np.asarray(zp_arr) != 0):
                    raise NotImplementedError(
                        f'Non-zero runtime QDQ zero_point at {node.name or dst!r} is unsupported '
                        'by ABI 5.0 (zero_point must be 0)'
                    )
                qdq_dtypes[dst] = 'u4' if zp_arr.dtype == np.uint8 else 'i4'
            qdq_bypass[dst] = qdq_bypass.get(src, src)

    # Build consumer counts for Conv+BN fusion
    consumers: dict[str, list] = {}
    for node in graph.node:
        for inp in node.input:
            consumers.setdefault(inp, []).append(node)

    # Resolve graph input (first non-initializer input)
    graph_inputs = [vi for vi in graph.input if vi.name not in inits]
    if len(graph_inputs) != 1:
        raise ValueError(f'Expected 1 runtime ONNX input, got {len(graph_inputs)}')
    in_name = graph_inputs[0].name
    in_shape_raw = shapes.get(in_name)
    if not in_shape_raw:
        raise ValueError(f'Could not infer shape of ONNX input {in_name!r}')
    if len(in_shape_raw) == 4:
        _, c_in, h_in, w_in = in_shape_raw
        in_shape_hwc = (h_in, w_in, c_in)
    elif len(in_shape_raw) == 2:
        _, c_in = in_shape_raw
        in_shape_hwc = (1, 1, c_in)
    else:
        raise ValueError(f'Unsupported ONNX input rank {len(in_shape_raw)}: {in_shape_raw}')

    act_scales_cfg: dict[str, float] = dict(qcfg.get('act_scales', {}))
    act_dtypes_cfg: dict[str, str] = dict(qcfg.get('act_dtypes', {}))
    weight_scales_cfg: dict[str, list[float]] = dict(qcfg.get('weight_scales', {}))
    prequant_weights: dict[str, list] = dict(qcfg.get('prequantized_weights', {}))
    prequant_bias: dict[str, list] = dict(qcfg.get('prequantized_bias', {}))
    requant_override: dict[str, dict] = dict(qcfg.get('requant_params', {}))
    add_weights_cfg: dict[str, list[int]] = dict(qcfg.get('add_weights', {}))

    in_dtype = qcfg.get('input_dtype', qdq_dtypes.get(in_name, 'i4'))
    in_scale = float(qcfg.get('input_scale', qdq_scales.get(in_name, act_scales_cfg.get(in_name, 1.0 / 7.0))))

    def resolve_name(name: str) -> str:
        return qdq_bypass.get(name, name)

    # Track live tensor alias map (for Flatten/Reshape/Squeeze/QDQ transparency), scales, dtypes, shapes
    alias: dict[str, str] = {in_name: in_name}
    t_scale: dict[str, float] = {in_name: in_scale}
    t_dtype: dict[str, str] = {in_name: in_dtype}
    t_shape: dict[str, tuple[int, int, int]] = {in_name: in_shape_hwc}

    def get_src(onnx_name: str) -> str:
        cur = onnx_name
        seen = set()
        while cur in alias and alias[cur] != cur and cur not in seen:
            seen.add(cur)
            cur = alias[cur]
        return cur

    def lookup_out_scale(onnx_out: str, fallback: float) -> float:
        if onnx_out in act_scales_cfg:
            return float(act_scales_cfg[onnx_out])
        if onnx_out in qdq_scales:
            return float(qdq_scales[onnx_out])
        return fallback

    def lookup_out_dtype(onnx_out: str, has_relu: bool) -> str:
        if onnx_out in act_dtypes_cfg:
            return act_dtypes_cfg[onnx_out]
        if onnx_out in qdq_dtypes:
            return qdq_dtypes[onnx_out]
        return 'u4' if has_relu else 'i4'

    ir_nodes: list[NodeIR] = []
    skip_nodes: set[int] = set()
    onnx_nodes = list(graph.node)

    # Identify the final graph output name (before trailing Softmax if any)
    raw_graph_output = graph.output[0].name
    final_linear_outputs = {raw_graph_output}
    for nd in onnx_nodes:
        if nd.op_type == 'Softmax' and nd.output[0] == raw_graph_output:
            final_linear_outputs.add(nd.input[0])

    idx = 0
    while idx < len(onnx_nodes):
        if idx in skip_nodes:
            idx += 1
            continue
        node = onnx_nodes[idx]
        op = node.op_type

        if op in ('QuantizeLinear', 'DequantizeLinear', 'Identity', 'Dropout'):
            dst = node.output[0]
            if dst in inits:
                idx += 1
                continue
            src = get_src(node.input[0])
            alias[dst] = src
            if dst in qdq_scales:
                t_scale[src] = qdq_scales[dst]
            idx += 1
            continue

        if op in ('Flatten', 'Reshape', 'Squeeze', 'Unsqueeze'):
            src = get_src(node.input[0])
            dst = node.output[0]
            h_src, w_src, c_src = t_shape[src]
            total_elems = h_src * w_src * c_src
            dst_shape = shapes.get(dst)
            if dst_shape is None and op == 'Flatten':
                axis = int(_get_attr(node, 'axis', 1))
                if axis == 1:
                    dst_shape = [1, total_elems]
            elif dst_shape is None and op == 'Reshape' and len(node.input) >= 2:
                sh_key = node.input[1] if node.input[1] in inits else resolve_name(node.input[1])
                if sh_key in inits:
                    raw_sh = [int(v) for v in np.asarray(inits[sh_key]).reshape(-1)]
                    in_nchw = [1, c_src, h_src, w_src]
                    resolved_sh: list[int] = []
                    neg_idx = -1
                    known_prod = 1
                    for i_dim, val in enumerate(raw_sh):
                        if val == 0 and i_dim < len(in_nchw):
                            val = in_nchw[i_dim]
                        if val == -1:
                            if neg_idx != -1:
                                resolved_sh = []
                                break
                            neg_idx = i_dim
                            resolved_sh.append(-1)
                        elif val > 0:
                            resolved_sh.append(val)
                            known_prod *= val
                        else:
                            resolved_sh = []
                            break
                    if resolved_sh:
                        if neg_idx != -1:
                            if known_prod > 0 and total_elems % known_prod == 0:
                                resolved_sh[neg_idx] = total_elems // known_prod
                            else:
                                resolved_sh = []
                        if resolved_sh:
                            dst_shape = resolved_sh

            if dst_shape is None or any(int(d) <= 0 for d in dst_shape):
                raise NotImplementedError(
                    f'Cannot verify shape-preserving {op} at {node.name or dst!r}: '
                    f'src HWC={t_shape[src]}, inferred dst_shape={dst_shape}'
                )
            dst_shape_i = [int(d) for d in dst_shape]
            if int(np.prod(dst_shape_i)) != total_elems:
                raise NotImplementedError(
                    f'{op} element count mismatch at {node.name or dst!r}: '
                    f'src HWC={t_shape[src]} ({total_elems}) vs dst_shape={dst_shape_i}'
                )
            if op == 'Flatten':
                axis = int(_get_attr(node, 'axis', 1))
                if axis != 1 and not (h_src == 1 and w_src == 1 and dst_shape_i == [1, c_src]):
                    raise NotImplementedError(
                        f'Flatten with axis={axis} unsupported at {node.name or dst!r} (only axis=1 supported)'
                    )

            # Collect downstream non-transparent consumers to guarantee safe layout aliasing
            real_consumers: list = []
            hits_graph_out = False
            stack = [dst]
            visited_c: set[str] = set()
            while stack:
                cur_t = stack.pop()
                if cur_t in visited_c:
                    continue
                visited_c.add(cur_t)
                if cur_t == raw_graph_output:
                    hits_graph_out = True
                for c_nd in consumers.get(cur_t, []):
                    if c_nd.op_type in ('QuantizeLinear', 'DequantizeLinear', 'Identity', 'Dropout'):
                        stack.append(c_nd.output[0])
                    else:
                        real_consumers.append(c_nd)

            same_4d = (
                len(dst_shape_i) == 4
                and dst_shape_i[0] == 1
                and (dst_shape_i[2], dst_shape_i[3], dst_shape_i[1]) == (h_src, w_src, c_src)
            )
            trivial_1x1 = (
                h_src == 1
                and w_src == 1
                and dst_shape_i in ([c_src], [1, c_src], [1, c_src, 1], [1, c_src, 1, 1])
                and not any(c.op_type in ('Conv', 'MaxPool', 'AveragePool', 'GlobalAveragePool') for c in real_consumers)
            )
            flat_2d_for_fc = (
                len(dst_shape_i) == 2
                and dst_shape_i == [1, total_elems]
                and not hits_graph_out
                and len(real_consumers) > 0
                and all(c.op_type in ('Gemm', 'MatMul') for c in real_consumers)
            )
            if not (same_4d or trivial_1x1 or flat_2d_for_fc):
                raise NotImplementedError(
                    f'Unsupported {op} layout transform at {node.name or dst!r}: '
                    f'src HWC={t_shape[src]} -> dst_shape={dst_shape_i}, '
                    f'consumers={[c.op_type for c in real_consumers]}'
                )
            alias[dst] = src
            idx += 1
            continue

        if op == 'Constant':
            val = _get_attr(node, 'value')
            if val is not None:
                inits[node.output[0]] = val
            idx += 1
            continue

        if op == 'Conv':
            src = get_src(node.input[0])
            raw_w_name = node.input[1]
            w_name = raw_w_name if raw_w_name in inits else resolve_name(raw_w_name)
            w_fp = inits[w_name].astype(np.float64)  # NCHW: (OC, IC/g, KH, KW)
            b_fp = None
            if len(node.input) >= 3 and node.input[2]:
                raw_b_name = node.input[2]
                b_name = raw_b_name if raw_b_name in inits else resolve_name(raw_b_name)
                if b_name in inits:
                    b_fp = inits[b_name].astype(np.float64)
            out_name = node.output[0]
            kh, kw = w_fp.shape[2], w_fp.shape[3]
            oc = int(w_fp.shape[0])
            groups = int(_get_attr(node, 'group', 1))
            strides = _get_attr(node, 'strides', [1, 1])
            h_in_cur, w_in_cur, _ = t_shape[src]
            pads = _resolve_auto_pad(node, h_in_cur, w_in_cur, kh, kw, int(strides[0]), int(strides[1]))
            dilations = _get_attr(node, 'dilations', [1, 1])
            if dilations != [1, 1]:
                raise ValueError(f'Dilated Conv unsupported by ABI 5.0: {node.name}')

            # Check for immediately following BatchNormalization to fold analytically
            next_consumers = consumers.get(out_name, [])
            if len(next_consumers) == 1 and next_consumers[0].op_type == 'BatchNormalization':
                bn = next_consumers[0]
                bn_idx = onnx_nodes.index(bn)
                skip_nodes.add(bn_idx)
                gamma = inits[bn.input[1]]
                beta = inits[bn.input[2]]
                mean = inits[bn.input[3]]
                var = inits[bn.input[4]]
                eps = float(_get_attr(bn, 'epsilon', 1e-5))
                w_fp, b_fp = _fold_conv_bn(w_fp, b_fp, gamma, beta, mean, var, eps)
                out_name = bn.output[0]

            # Check for immediately following Relu / Clip(0, max)
            has_relu = False
            clip_max_fp: float | None = None
            post_consumers = consumers.get(out_name, [])
            relu_out_name = out_name
            if len(post_consumers) == 1 and post_consumers[0].op_type in ('Relu', 'Clip'):
                cand = post_consumers[0]
                is_relu_like = cand.op_type == 'Relu'
                if cand.op_type == 'Clip':
                    cmin = _get_attr(cand, 'min', None)
                    if cmin is None and len(cand.input) >= 2 and cand.input[1] and cand.input[1] in inits:
                        cmin = float(inits[cand.input[1]])
                    cmax = _get_attr(cand, 'max', None)
                    if cmax is None and len(cand.input) >= 3 and cand.input[2] and cand.input[2] in inits:
                        cmax = float(inits[cand.input[2]])
                    if cmin is not None and abs(float(cmin)) < 1e-6:
                        if cmax is not None and math.isfinite(float(cmax)):
                            if float(cmax) <= 0.0:
                                raise NotImplementedError(f'Clip with non-positive max={cmax} unsupported at {cand.name!r}')
                            clip_max_fp = float(cmax)
                        is_relu_like = True
                if is_relu_like:
                    has_relu = True
                    skip_nodes.add(onnx_nodes.index(cand))
                    relu_out_name = cand.output[0]

            # Quantize weights & bias (matching NPU layout: standard (OC,KH,KW,IC), depthwise (OC,KH,KW))
            is_final_logits = (relu_out_name in final_linear_outputs) and not has_relu and (relu_out_name not in act_scales_cfg)
            key_candidates = [relu_out_name, out_name, node.name, node.output[0]]
            exp_w_scales = next((weight_scales_cfg[k] for k in key_candidates if k in weight_scales_cfg), None)
            if exp_w_scales is None and not is_final_logits and w_name in qdq_weight_scales:
                # Only reuse QDQ weight scales directly when values fit INT4 [-8, 7]
                cand_sc = qdq_weight_scales[w_name]
                if cand_sc.size in (1, oc):
                    sv = cand_sc.reshape((oc, 1, 1, 1)) if cand_sc.size == oc else cand_sc.reshape((1, 1, 1, 1))
                    if float(np.max(np.abs(w_fp / np.maximum(sv, 1e-12)))) <= 7.5:
                        exp_w_scales = cand_sc
            if any(k in prequant_weights for k in key_candidates):
                k_hit = next(k for k in key_candidates if k in prequant_weights)
                w_q_list = prequant_weights[k_hit]
                w_scales = np.asarray(exp_w_scales if exp_w_scales is not None else [1.0] * oc, dtype=np.float64)
                if w_scales.size == 1:
                    w_scales = np.broadcast_to(w_scales, (oc,)).copy()
            else:
                w_q, w_scales = _quantize_weights_per_channel(w_fp, exp_w_scales, global_scale=is_final_logits)
                if groups != 1:
                    w_q_list = w_q.reshape(oc, kh, kw).tolist()
                else:
                    w_q_list = np.transpose(w_q, (0, 2, 3, 1)).reshape(-1).tolist()

            in_sc = t_scale[src]
            acc_scales = in_sc * w_scales
            if any(k in prequant_bias for k in key_candidates):
                k_hit = next(k for k in key_candidates if k in prequant_bias)
                b_q_list = _clamp_bias_18(np.asarray(prequant_bias[k_hit], dtype=np.int64))
            else:
                b_arr = np.zeros(oc, dtype=np.float64) if b_fp is None else b_fp
                b_q_list = _clamp_bias_18(np.rint(b_arr / np.maximum(acc_scales, 1e-12)))

            h_in_cur, w_in_cur, _ = t_shape[src]
            oh = (h_in_cur + pads[0] + pads[2] - kh) // strides[0] + 1
            ow = (w_in_cur + pads[1] + pads[3] - kw) // strides[1] + 1

            conv_raw = f'{relu_out_name}__conv_i32' if not is_final_logits else relu_out_name
            ir_nodes.append(NodeIR(
                op='Conv', inputs=[src], output=conv_raw, name=node.name or conv_raw,
                attrs=dict(channels=oc, kernel=[kh, kw], stride=list(strides), pads=list(pads),
                           groups=groups, weights=w_q_list, bias=b_q_list),
            ))
            t_shape[conv_raw] = (oh, ow, oc)
            t_dtype[conv_raw] = 'i32'
            t_scale[conv_raw] = float(acc_scales[0]) if is_final_logits else float(np.mean(acc_scales))

            if not is_final_logits:
                out_dtype = lookup_out_dtype(relu_out_name, has_relu)
                if any(k in requant_override for k in key_candidates):
                    ro = next(requant_override[k] for k in key_candidates if k in requant_override)
                    mults, shifts = list(ro['multiplier']), list(ro['shift'])
                    out_sc = float(ro.get('scale', lookup_out_scale(relu_out_name, in_sc)))
                    clamp = list(ro.get('clamp', [0, 15] if out_dtype == 'u4' else [-8, 7]))
                else:
                    out_sc = lookup_out_scale(relu_out_name, float(np.max(acc_scales) * 8.0))
                    mults, shifts = [], []
                    for ch in range(oc):
                        qm, qs = float_scale_to_q15(acc_scales[ch] / max(out_sc, 1e-12))
                        mults.append(qm)
                        shifts.append(qs)
                    clamp = [0, 15] if out_dtype == 'u4' else ([0, 7] if has_relu else [-8, 7])
                if clip_max_fp is not None:
                    q_hi = int(round(clip_max_fp / max(out_sc, 1e-12)))
                    clamp = [clamp[0], max(clamp[0], min(clamp[1], q_hi))]
                req_out = relu_out_name if not has_relu or out_dtype == 'u4' else f'{relu_out_name}__req'
                ir_nodes.append(NodeIR(
                    op='Requant', inputs=[conv_raw], output=req_out, name=f'{node.name or relu_out_name}_req',
                    attrs=dict(multiplier=mults, shift=shifts, clamp=clamp, dtype=out_dtype),
                ))
                t_shape[req_out] = (oh, ow, oc)
                t_dtype[req_out] = out_dtype
                t_scale[req_out] = out_sc
                if has_relu and req_out != relu_out_name:
                    ir_nodes.append(NodeIR(op='ReLU', inputs=[req_out], output=relu_out_name))
                    t_shape[relu_out_name] = (oh, ow, oc)
                    t_dtype[relu_out_name] = out_dtype
                    t_scale[relu_out_name] = out_sc

            if out_name != relu_out_name:
                alias[out_name] = relu_out_name
            if node.output[0] != relu_out_name:
                alias[node.output[0]] = relu_out_name
            idx += 1
            continue

        if op in ('Gemm', 'MatMul'):
            src = get_src(node.input[0])
            raw_w_name = node.input[1]
            w_name = raw_w_name if raw_w_name in inits else resolve_name(raw_w_name)
            w_fp = inits[w_name].astype(np.float64)
            if op == 'Gemm':
                trans_a = int(_get_attr(node, 'transA', 0))
                if trans_a != 0:
                    raise NotImplementedError(f'Gemm transA={trans_a} unsupported at {node.name!r}')
                trans_b = int(_get_attr(node, 'transB', 0))  # ONNX spec default is transB=0
                alpha = float(_get_attr(node, 'alpha', 1.0))
                beta = float(_get_attr(node, 'beta', 1.0))
            else:
                trans_b = 0
                alpha = 1.0
                beta = 1.0
            if trans_b == 0:
                w_fp = w_fp.T  # normalize to (OC, K)
            w_fp = w_fp * alpha
            oc, k_in = w_fp.shape
            out_name = node.output[0]
            b_fp = None
            if op == 'Gemm' and len(node.input) >= 3 and node.input[2]:
                raw_b_name = node.input[2]
                b_name = raw_b_name if raw_b_name in inits else resolve_name(raw_b_name)
                if b_name in inits:
                    b_fp = inits[b_name].astype(np.float64) * beta
            elif op == 'MatMul':
                next_c = consumers.get(out_name, [])
                if len(next_c) == 1 and next_c[0].op_type == 'Add':
                    add_nd = next_c[0]
                    other = add_nd.input[1] if add_nd.input[0] == out_name else add_nd.input[0]
                    b_key = other if other in inits else resolve_name(other)
                    if b_key in inits:
                        skip_nodes.add(onnx_nodes.index(add_nd))
                        b_fp = inits[b_key].astype(np.float64).reshape(-1)
                        out_name = add_nd.output[0]

            # If input tensor was 3D (H, W, C) with H*W > 1, permute weight columns from NCHW (C,H,W) -> NHWC (H,W,C)
            h_in_cur, w_in_cur, c_in_cur = t_shape[src]
            if h_in_cur * w_in_cur > 1 and h_in_cur * w_in_cur * c_in_cur == k_in:
                w_fp = np.transpose(w_fp.reshape(oc, c_in_cur, h_in_cur, w_in_cur), (0, 2, 3, 1)).reshape(oc, k_in)

            is_final_logits = (out_name in final_linear_outputs) and (out_name not in act_scales_cfg)
            key_candidates = [out_name, node.name, node.output[0]]
            exp_w_scales = next((weight_scales_cfg[k] for k in key_candidates if k in weight_scales_cfg), None)
            if any(k in prequant_weights for k in key_candidates):
                k_hit = next(k for k in key_candidates if k in prequant_weights)
                w_q_list = prequant_weights[k_hit]
                w_scales = np.asarray(exp_w_scales if exp_w_scales is not None else [1.0] * oc, dtype=np.float64)
                if w_scales.size == 1:
                    w_scales = np.broadcast_to(w_scales, (oc,)).copy()
            else:
                # Final raw INT32 logits MUST use a single global weight scale so argmax & Softmax are exact!
                w_q, w_scales = _quantize_weights_per_channel(w_fp, exp_w_scales, global_scale=is_final_logits)
                w_q_list = w_q.reshape(-1).tolist()

            in_sc = t_scale[src]
            acc_scales = in_sc * w_scales
            if any(k in prequant_bias for k in key_candidates):
                k_hit = next(k for k in key_candidates if k in prequant_bias)
                b_q_list = _clamp_bias_18(np.asarray(prequant_bias[k_hit], dtype=np.int64))
            else:
                b_arr = np.zeros(oc, dtype=np.float64) if b_fp is None else b_fp
                b_q_list = _clamp_bias_18(np.rint(b_arr / np.maximum(acc_scales, 1e-12)))

            fc_raw = out_name if is_final_logits else f'{out_name}__fc_i32'
            ir_nodes.append(NodeIR(
                op='FC', inputs=[src], output=fc_raw, name=node.name or fc_raw,
                attrs=dict(channels=oc, weights=w_q_list, bias=b_q_list),
            ))
            t_shape[fc_raw] = (1, 1, oc)
            t_dtype[fc_raw] = 'i32'
            t_scale[fc_raw] = float(acc_scales[0]) if is_final_logits else float(np.mean(acc_scales))

            if not is_final_logits:
                out_dtype = lookup_out_dtype(out_name, False)
                out_sc = lookup_out_scale(out_name, float(np.max(acc_scales) * 8.0))
                mults, shifts = [], []
                for ch in range(oc):
                    qm, qs = float_scale_to_q15(acc_scales[ch] / max(out_sc, 1e-12))
                    mults.append(qm)
                    shifts.append(qs)
                ir_nodes.append(NodeIR(
                    op='Requant', inputs=[fc_raw], output=out_name,
                    attrs=dict(multiplier=mults, shift=shifts,
                               clamp=[0, 15] if out_dtype == 'u4' else [-8, 7], dtype=out_dtype),
                ))
                t_shape[out_name] = (1, 1, oc)
                t_dtype[out_name] = out_dtype
                t_scale[out_name] = out_sc
            idx += 1
            continue

        if op in ('MaxPool', 'AveragePool', 'GlobalAveragePool'):
            src = get_src(node.input[0])
            h_cur, w_cur, c_cur = t_shape[src]
            out_name = node.output[0]
            if op == 'GlobalAveragePool':
                kh, kw, sh, sw = h_cur, w_cur, h_cur, w_cur
                pads = [0, 0, 0, 0]
                ir_op = 'AvgPool'
            else:
                ceil_mode = int(_get_attr(node, 'ceil_mode', 0))
                if ceil_mode != 0:
                    raise NotImplementedError(
                        f'{op} with ceil_mode={ceil_mode} is unsupported by ABI 5.0 at {node.name!r}'
                    )
                pool_dilations = _get_attr(node, 'dilations', [1, 1])
                if list(pool_dilations) != [1, 1]:
                    raise NotImplementedError(
                        f'{op} with dilations={pool_dilations} is unsupported by ABI 5.0 at {node.name!r}'
                    )
                if op == 'MaxPool' and int(_get_attr(node, 'storage_order', 0)) != 0:
                    raise NotImplementedError(
                        f'MaxPool with non-zero storage_order is unsupported at {node.name!r}'
                    )
                k = _get_attr(node, 'kernel_shape', [2, 2])
                s = _get_attr(node, 'strides', k)
                kh, kw = int(k[0]), int(k[1])
                sh, sw = int(s[0]), int(s[1])
                pads = _resolve_auto_pad(node, h_cur, w_cur, kh, kw, sh, sw)
                ir_op = 'MaxPool' if op == 'MaxPool' else 'AvgPool'
                if ir_op == 'AvgPool' and any(int(p) != 0 for p in pads):
                    count_include_pad = int(_get_attr(node, 'count_include_pad', 0))
                    if count_include_pad == 0:
                        raise NotImplementedError(
                            f'Padded AveragePool with count_include_pad=0 at {node.name!r} '
                            'has spatially varying border divisors unsupported by uniform ABI 5.0 AvgPool'
                        )
            oh = (h_cur + pads[0] + pads[2] - kh) // sh + 1
            ow = (w_cur + pads[1] + pads[3] - kw) // sw + 1
            if ir_op == 'MaxPool':
                ir_nodes.append(NodeIR(
                    op='MaxPool', inputs=[src], output=out_name, name=node.name or out_name,
                    attrs=dict(kernel=[kh, kw], stride=[sh, sw], pads=list(pads)),
                ))
                t_shape[out_name] = (oh, ow, c_cur)
                t_dtype[out_name] = t_dtype[src]
                t_scale[out_name] = t_scale[src]
            else:
                raw_avg = f'{out_name}__avg_i32'
                ir_nodes.append(NodeIR(
                    op='AvgPool', inputs=[src], output=raw_avg, name=node.name or raw_avg,
                    attrs=dict(kernel=[kh, kw], stride=[sh, sw], pads=list(pads)),
                ))
                out_dtype = lookup_out_dtype(out_name, t_dtype[src] == 'u4')
                out_sc = lookup_out_scale(out_name, t_scale[src])
                # AvgPool sums kh*kw elements; divide by kh*kw inside Requant!
                key_candidates = [out_name, node.name]
                if any(k in requant_override for k in key_candidates):
                    ro = next(requant_override[k] for k in key_candidates if k in requant_override)
                    mults, shifts = list(ro['multiplier']), list(ro['shift'])
                    clamp = list(ro.get('clamp', [0, 15] if out_dtype == 'u4' else [-8, 7]))
                else:
                    qm, qs = float_scale_to_q15(t_scale[src] / (kh * kw * max(out_sc, 1e-12)))
                    mults, shifts = [qm], [qs]
                    clamp = [0, 15] if out_dtype == 'u4' else [-8, 7]
                ir_nodes.append(NodeIR(
                    op='Requant', inputs=[raw_avg], output=out_name,
                    attrs=dict(multiplier=mults, shift=shifts, clamp=clamp, dtype=out_dtype),
                ))
                t_shape[raw_avg] = (oh, ow, c_cur)
                t_dtype[raw_avg] = 'i32'
                t_scale[raw_avg] = t_scale[src] / (kh * kw)
                t_shape[out_name] = (oh, ow, c_cur)
                t_dtype[out_name] = out_dtype
                t_scale[out_name] = out_sc
            idx += 1
            continue

        if op == 'Add':
            src1 = get_src(node.input[0])
            src2 = get_src(node.input[1])
            out_name = node.output[0]
            has_relu = False
            relu_out_name = out_name
            post_c = consumers.get(out_name, [])
            if len(post_c) == 1 and post_c[0].op_type == 'Relu':
                has_relu = True
                skip_nodes.add(onnx_nodes.index(post_c[0]))
                relu_out_name = post_c[0].output[0]

            key_candidates = [relu_out_name, out_name, node.name]
            out_dtype = lookup_out_dtype(relu_out_name, has_relu)
            out_sc = lookup_out_scale(relu_out_name, max(t_scale[src1], t_scale[src2]))
            if any(k in add_weights_cfg for k in key_candidates):
                wa, wb = next(add_weights_cfg[k] for k in key_candidates if k in add_weights_cfg)
            else:
                wa, wb, qm, qs = _best_add_weights(t_scale[src1], t_scale[src2], out_sc)
            if any(k in requant_override for k in key_candidates):
                ro = next(requant_override[k] for k in key_candidates if k in requant_override)
                mults, shifts = list(ro['multiplier']), list(ro['shift'])
                clamp = list(ro.get('clamp', [0, 15] if out_dtype == 'u4' else [-8, 7]))
            else:
                _, _, qm, qs = _best_add_weights(t_scale[src1], t_scale[src2], out_sc)
                mults, shifts = [qm], [qs]
                clamp = [0, 15] if out_dtype == 'u4' else ([0, 7] if has_relu else [-8, 7])

            raw_add = f'{relu_out_name}__add_i32'
            ir_nodes.append(NodeIR(
                op='Add', inputs=[src1, src2], output=raw_add, name=node.name or raw_add,
                attrs=dict(weights=[int(wa), int(wb)]),
            ))
            ir_nodes.append(NodeIR(
                op='Requant', inputs=[raw_add], output=relu_out_name,
                attrs=dict(multiplier=mults, shift=shifts, clamp=clamp, dtype=out_dtype),
            ))
            t_shape[raw_add] = t_shape[src1]
            t_dtype[raw_add] = 'i32'
            t_scale[raw_add] = out_sc
            t_shape[relu_out_name] = t_shape[src1]
            t_dtype[relu_out_name] = out_dtype
            t_scale[relu_out_name] = out_sc
            if out_name != relu_out_name:
                alias[out_name] = relu_out_name
            idx += 1
            continue

        if op == 'Relu':
            src = get_src(node.input[0])
            out_name = node.output[0]
            ir_nodes.append(NodeIR(op='ReLU', inputs=[src], output=out_name, name=node.name or out_name))
            t_shape[out_name] = t_shape[src]
            t_dtype[out_name] = t_dtype[src]
            t_scale[out_name] = t_scale[src]
            idx += 1
            continue

        if op == 'Softmax':
            src = get_src(node.input[0])
            out_name = node.output[0]
            onnx_axis = int(_get_attr(node, 'axis', -1))
            src_h, src_w, _ = t_shape[src]
            # ONNX Softmax over class/channel axis maps to HWC axis=-1 when rank-2 (1,C) or NCHW axis=1.
            if onnx_axis in (-1, 1) and (src_h == 1 and src_w == 1 or onnx_axis == 1):
                hwc_axis = -1
            elif onnx_axis == 2 and (src_h > 1 or src_w > 1):
                hwc_axis = 0
            elif onnx_axis == 3 and (src_h > 1 or src_w > 1):
                hwc_axis = 1
            else:
                raise NotImplementedError(f'Unsupported Softmax axis={onnx_axis} for HWC shape {t_shape[src]}')
            ir_nodes.append(NodeIR(
                op='CPUFallback', inputs=[src], output=out_name,
                backend='cpu_fallback', name=node.name or out_name,
                attrs=dict(operator='Softmax', axis=hwc_axis, input_scale=float(t_scale[src])),
            ))
            t_shape[out_name] = t_shape[src]
            t_dtype[out_name] = 'f32'
            t_scale[out_name] = 1.0
            idx += 1
            continue

        raise NotImplementedError(f'Unsupported ONNX operator {op!r} at node {node.name!r}')

    final_out = get_src(raw_graph_output)
    return GraphIR(
        input=TensorDesc(in_name, in_shape_hwc, in_dtype, in_scale),
        nodes=ir_nodes,
        output=final_out,
        metadata=dict(qcfg.get('metadata', {}), source='onnx_frontend', input_scale=in_scale),
    )
