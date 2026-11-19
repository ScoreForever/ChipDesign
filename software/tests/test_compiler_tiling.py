"""Comprehensive unit + RTL bit-exact tests for software/compiler/ (ONNX + 3D Tiling)."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from software.compiler import (  # noqa: E402
    GraphIR,
    NodeIR,
    ReferenceBackend,
    TensorDesc,
    TiledPlan,
    TiledRuntime,
    compile_graph_to_plan,
    load_onnx_to_ir,
    oracle_reference,
    verify_plan,
)


def _make_conv_bn_relu_onnx(in_c=4, out_c=16, h=12, w=12, k=3, s=1, p=1) -> onnx.ModelProto:
    rng = np.random.default_rng(7)
    w_arr = rng.normal(0.0, 0.4, size=(out_c, in_c, k, k)).astype(np.float32)
    b_arr = rng.normal(0.0, 0.1, size=(out_c,)).astype(np.float32)
    gamma = rng.uniform(0.7, 1.3, size=(out_c,)).astype(np.float32)
    beta = rng.normal(0.0, 0.1, size=(out_c,)).astype(np.float32)
    mean = rng.normal(0.0, 0.2, size=(out_c,)).astype(np.float32)
    var = rng.uniform(0.5, 1.5, size=(out_c,)).astype(np.float32)

    inits = [
        numpy_helper.from_array(w_arr, 'conv_w'),
        numpy_helper.from_array(b_arr, 'conv_b'),
        numpy_helper.from_array(gamma, 'bn_gamma'),
        numpy_helper.from_array(beta, 'bn_beta'),
        numpy_helper.from_array(mean, 'bn_mean'),
        numpy_helper.from_array(var, 'bn_var'),
    ]
    nodes = [
        helper.make_node('Conv', ['x', 'conv_w', 'conv_b'], ['c1'],
                         kernel_shape=[k, k], strides=[s, s], pads=[p, p, p, p], name='conv1'),
        helper.make_node('BatchNormalization', ['c1', 'bn_gamma', 'bn_beta', 'bn_mean', 'bn_var'],
                         ['bn1'], epsilon=1e-5, name='bn1'),
        helper.make_node('Relu', ['bn1'], ['y'], name='relu1'),
    ]
    oh = (h + 2 * p - k) // s + 1
    ow = (w + 2 * p - k) // s + 1
    graph = helper.make_graph(
        nodes, 'conv_bn_relu',
        [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, in_c, h, w])],
        [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, out_c, oh, ow])],
        initializer=inits,
    )
    return helper.make_model(graph, opset_imports=[helper.make_opsetid('', 13)])


class CompilerTilingTests(unittest.TestCase):
    def test_01_onnx_conv_bn_relu_folding_bit_exact(self) -> None:
        model = _make_conv_bn_relu_onnx(in_c=4, out_c=16, h=12, w=12)
        graph = load_onnx_to_ir(model, quant_config={'input_scale': 0.125, 'input_dtype': 'i4', 'act_scales': {'y': 0.1}})
        self.assertEqual(graph.nodes[0].op, 'Conv')
        self.assertEqual(graph.nodes[1].op, 'Requant')
        plan = compile_graph_to_plan(graph)
        x = np.random.default_rng(1).integers(-8, 8, size=(12, 12, 4), dtype=np.int64)
        res = verify_plan(graph, plan, x)
        self.assertTrue(res['oracle_vs_tiled_ref_exact'])
        self.assertEqual(res['max_abs_diff'], 0)

    def test_02_resnet18_stem_7x7_s2_p3_and_maxpool_3x3_s2_p1(self) -> None:
        rng = np.random.default_rng(2)
        oc = 16
        w_conv = rng.integers(-8, 8, size=(oc, 7, 7, 3), dtype=np.int64).reshape(-1).tolist()
        b_conv = rng.integers(-64, 64, size=(oc,), dtype=np.int64).tolist()
        graph = GraphIR(
            input=TensorDesc('x', (32, 32, 3), 'i4'),
            nodes=[
                NodeIR('Conv', ['x'], 'c0', dict(channels=oc, kernel=[7, 7], stride=[2, 2], pads=[3, 3, 3, 3],
                                                 groups=1, weights=w_conv, bias=b_conv)),
                NodeIR('Requant', ['c0'], 'q0', dict(multiplier=[0x4000] * oc, shift=[-4] * oc, clamp=[0, 15], dtype='u4')),
                NodeIR('MaxPool', ['q0'], 'p0', dict(kernel=[3, 3], stride=[2, 2], pads=[1, 1, 1, 1])),
            ],
            output='p0',
        )
        plan = compile_graph_to_plan(graph)
        x = rng.integers(-8, 8, size=(32, 32, 3), dtype=np.int64)
        res = verify_plan(graph, plan, x)
        self.assertTrue(res['oracle_vs_tiled_ref_exact'])
        self.assertEqual(res['output_shape'], [8, 8, 16])

    def test_03_forced_k_tiling_above_max_k_1024(self) -> None:
        # 3x3 Conv with IC=128 => K = 1152 > 1024 forces lossless INT32 K-tiling!
        rng = np.random.default_rng(3)
        ic, oc = 128, 32
        w = rng.integers(-4, 4, size=(oc, 3, 3, ic), dtype=np.int64).reshape(-1).tolist()
        b = rng.integers(-200, 200, size=(oc,), dtype=np.int64).tolist()
        graph = GraphIR(
            input=TensorDesc('x', (6, 6, ic), 'u4'),
            nodes=[
                NodeIR('Conv', ['x'], 'c1', dict(channels=oc, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1],
                                                 groups=1, weights=w, bias=b)),
                NodeIR('Requant', ['c1'], 'y', dict(multiplier=[0x5000] * oc, shift=[-6] * oc, clamp=[0, 15], dtype='u4')),
            ],
            output='y',
        )
        plan = compile_graph_to_plan(graph)
        # Must use multiple k_slices and HostPostOp Requant
        self.assertGreater(len(plan.groups), 1)
        self.assertTrue(any(g.k_slice != (0, ic) for g in plan.groups))
        x = rng.integers(0, 16, size=(6, 6, ic), dtype=np.int64)
        res = verify_plan(graph, plan, x)
        self.assertTrue(res['oracle_vs_tiled_ref_exact'])
        self.assertEqual(res['max_abs_diff'], 0)

    def test_04_forced_oc_tiling_fc_and_residual_add_over_256(self) -> None:
        rng = np.random.default_rng(4)
        # FC 64 -> 300 (> 256) and residual Add on 300 channels!
        w_fc = rng.integers(-6, 6, size=(300, 64), dtype=np.int64).reshape(-1).tolist()
        b_fc = rng.integers(-50, 50, size=(300,), dtype=np.int64).tolist()
        graph = GraphIR(
            input=TensorDesc('x', (1, 1, 64), 'u4'),
            nodes=[
                NodeIR('FC', ['x'], 'fc_a', dict(channels=300, weights=w_fc, bias=b_fc)),
                NodeIR('Requant', ['fc_a'], 'qa', dict(multiplier=[0x4000] * 300, shift=[-4] * 300, clamp=[0, 15], dtype='u4')),
                NodeIR('Add', ['qa', 'qa'], 'add_raw', dict(weights=[3, 5])),
                NodeIR('Requant', ['add_raw'], 'y', dict(multiplier=[0x4000] * 300, shift=[-3] * 300, clamp=[0, 15], dtype='u4')),
            ],
            output='y',
        )
        plan = compile_graph_to_plan(graph)
        x = rng.integers(0, 16, size=(1, 1, 64), dtype=np.int64)
        res = verify_plan(graph, plan, x)
        self.assertTrue(res['oracle_vs_tiled_ref_exact'])
        self.assertEqual(res['output_shape'], [1, 1, 300])

    def test_05_spatial_tiling_weight_stationary_reuse(self) -> None:
        # 64x64x16 => 32,768 B > 16,384 B SRAM! Must spatially tile with 1 MODEL_COMMIT!
        rng = np.random.default_rng(5)
        oc = 16
        w = rng.integers(-5, 5, size=(oc, 3, 3, 16), dtype=np.int64).reshape(-1).tolist()
        b = rng.integers(-30, 30, size=(oc,), dtype=np.int64).tolist()
        graph = GraphIR(
            input=TensorDesc('x', (64, 64, 16), 'u4'),
            nodes=[
                NodeIR('Conv', ['x'], 'c', dict(channels=oc, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1],
                                                groups=1, weights=w, bias=b)),
                NodeIR('Requant', ['c'], 'y', dict(multiplier=[0x4000] * oc, shift=[-5] * oc, clamp=[0, 15], dtype='u4')),
            ],
            output='y',
        )
        plan = compile_graph_to_plan(graph)
        x = rng.integers(0, 16, size=(64, 64, 16), dtype=np.int64)
        res = verify_plan(graph, plan, x)
        self.assertTrue(res['oracle_vs_tiled_ref_exact'])
        # Weight-stationary proof: ALL spatial tiles share exactly ONE MODEL_COMMIT!
        self.assertEqual(res['ref_telemetry']['model_loads'], 1)
        self.assertGreater(res['ref_telemetry']['tile_infers'], 1)

    def test_06_small_layer_subgraph_fusion(self) -> None:
        rng = np.random.default_rng(6)
        w1 = rng.integers(-4, 4, size=(8, 3, 3, 4), dtype=np.int64).reshape(-1).tolist()
        w2 = rng.integers(-4, 4, size=(8, 3, 3, 8), dtype=np.int64).reshape(-1).tolist()
        graph = GraphIR(
            input=TensorDesc('x', (8, 8, 4), 'u4'),
            nodes=[
                NodeIR('Conv', ['x'], 'c1', dict(channels=8, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1], groups=1, weights=w1, bias=[0] * 8)),
                NodeIR('Requant', ['c1'], 'q1', dict(multiplier=[0x4000] * 8, shift=[-3] * 8, clamp=[0, 15], dtype='u4')),
                NodeIR('Conv', ['q1'], 'c2', dict(channels=8, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1], groups=1, weights=w2, bias=[0] * 8)),
                NodeIR('Requant', ['c2'], 'y', dict(multiplier=[0x4000] * 8, shift=[-3] * 8, clamp=[0, 15], dtype='u4')),
            ],
            output='y',
        )
        plan = compile_graph_to_plan(graph, enable_fusion=True)
        self.assertEqual(len(plan.groups), 1)
        self.assertTrue(plan.groups[0].is_fused_subgraph)
        x = rng.integers(0, 16, size=(8, 8, 4), dtype=np.int64)
        res = verify_plan(graph, plan, x)
        self.assertTrue(res['oracle_vs_tiled_ref_exact'])

    def test_07_onnx_cpu_fallback_softmax(self) -> None:
        rng = np.random.default_rng(7)
        w = rng.normal(0.0, 0.3, size=(10, 16)).astype(np.float32)
        b = rng.normal(0.0, 0.1, size=(10,)).astype(np.float32)
        graph_proto = helper.make_graph(
            [
                helper.make_node('Gemm', ['x', 'w', 'b'], ['logits'], transB=1, name='fc'),
                helper.make_node('Softmax', ['logits'], ['probs'], axis=1, name='sm'),
            ],
            'fc_softmax',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 16])],
            [helper.make_tensor_value_info('probs', TensorProto.FLOAT, [1, 10])],
            initializer=[numpy_helper.from_array(w, 'w'), numpy_helper.from_array(b, 'b')],
        )
        model = helper.make_model(graph_proto, opset_imports=[helper.make_opsetid('', 13)])
        ir = load_onnx_to_ir(model, quant_config={'input_scale': 0.1, 'input_dtype': 'u4'})
        self.assertEqual(ir.nodes[-1].op, 'CPUFallback')
        plan = compile_graph_to_plan(ir)
        x = rng.integers(0, 16, size=(1, 1, 16), dtype=np.int64)
        out, _ = TiledRuntime(ReferenceBackend()).run(plan, x)
        self.assertAlmostEqual(float(out.sum()), 1.0, places=5)
        res = verify_plan(ir, plan, x)
        self.assertTrue(res['oracle_vs_tiled_ref_exact'])

    def test_08_plan_save_load_roundtrip(self) -> None:
        rng = np.random.default_rng(8)
        w = rng.integers(-4, 4, size=(16, 3, 3, 16), dtype=np.int64).reshape(-1).tolist()
        graph = GraphIR(
            input=TensorDesc('x', (40, 40, 16), 'u4'),
            nodes=[
                NodeIR('Conv', ['x'], 'c', dict(channels=16, kernel=[3, 3], stride=[2, 2], pads=[1, 1, 1, 1], groups=1, weights=w, bias=[1] * 16)),
                NodeIR('Requant', ['c'], 'y', dict(multiplier=[0x4000] * 16, shift=[-5] * 16, clamp=[0, 15], dtype='u4')),
            ],
            output='y',
        )
        plan = compile_graph_to_plan(graph)
        x = rng.integers(0, 16, size=(40, 40, 16), dtype=np.int64)
        with tempfile.TemporaryDirectory() as td:
            plan.save(td)
            reloaded = TiledPlan.load(td)
            res = verify_plan(reloaded.graph, reloaded, x)
            self.assertTrue(res['oracle_vs_tiled_ref_exact'])
            self.assertEqual(reloaded.summary()['tile_calls'], plan.summary()['tile_calls'])

    @unittest.skipUnless(shutil.which('iverilog') and shutil.which('vvp'), 'iverilog/vvp required')
    def test_09_mini_resnet_block_rtl_bit_exact(self) -> None:
        # Mini ResNet residual block (Conv 3x3 -> Requant(u4) -> Conv 3x3 -> Requant(i4) -> Add -> Requant(u4) -> GAP -> FC)
        rng = np.random.default_rng(9)
        w1 = rng.integers(-4, 4, size=(8, 3, 3, 8), dtype=np.int64).reshape(-1).tolist()
        w2 = rng.integers(-4, 4, size=(8, 3, 3, 8), dtype=np.int64).reshape(-1).tolist()
        w_fc = rng.integers(-4, 4, size=(4, 8), dtype=np.int64).reshape(-1).tolist()
        graph = GraphIR(
            input=TensorDesc('x', (8, 8, 8), 'u4'),
            nodes=[
                NodeIR('Conv', ['x'], 'c1', dict(channels=8, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1], groups=1, weights=w1, bias=[2] * 8)),
                NodeIR('Requant', ['c1'], 'q1', dict(multiplier=[0x4000] * 8, shift=[-4] * 8, clamp=[0, 15], dtype='u4')),
                NodeIR('Conv', ['q1'], 'c2', dict(channels=8, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1], groups=1, weights=w2, bias=[-1] * 8)),
                NodeIR('Requant', ['c2'], 'q2', dict(multiplier=[0x4000] * 8, shift=[-4] * 8, clamp=[-8, 7], dtype='i4')),
                NodeIR('Add', ['x', 'q2'], 'add', dict(weights=[1, 1])),
                NodeIR('Requant', ['add'], 'q3', dict(multiplier=[0x4000] * 8, shift=[-1] * 8, clamp=[0, 15], dtype='u4')),
                NodeIR('AvgPool', ['q3'], 'gap', dict(kernel=[8, 8], stride=[8, 8], pads=[0, 0, 0, 0])),
                NodeIR('Requant', ['gap'], 'qgap', dict(multiplier=[0x4000] * 8, shift=[-5] * 8, clamp=[0, 15], dtype='u4')),
                NodeIR('FC', ['qgap'], 'logits', dict(channels=4, weights=w_fc, bias=[0, 1, -1, 2])),
            ],
            output='logits',
        )
        plan = compile_graph_to_plan(graph, enable_fusion=True)
        x = rng.integers(0, 16, size=(8, 8, 8), dtype=np.int64)
        res = verify_plan(graph, plan, x, run_rtl=True)
        self.assertTrue(res['oracle_vs_tiled_ref_exact'])
        self.assertTrue(res['oracle_vs_rtl_exact'])
        self.assertGreater(res['rtl_telemetry']['npu_cycles'], 0)

    def test_10_onnx_qdq_weights_gemm_defaults_softmax_scale_and_avgpool_pad(self) -> None:
        # 1) QDQ per-channel weight DequantizeLinear must apply scale & zero_point cleanly
        w_int8 = np.array([[[[4]], [[-4]]], [[[-2]], [[6]]]], dtype=np.int8)  # (2, 2, 1, 1)
        w_scales = np.array([0.05, 0.25], dtype=np.float32)
        w_zp = np.array([0, 0], dtype=np.int8)
        g_qdq = helper.make_graph(
            [
                helper.make_node('DequantizeLinear', ['wq', 'ws', 'wzp'], ['wf'], axis=0),
                helper.make_node('Conv', ['x', 'wf'], ['y'], kernel_shape=[1, 1], strides=[1, 1], pads=[0, 0, 0, 0]),
            ],
            'qdq_conv',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 2, 2, 2])],
            [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, 2, 2, 2])],
            initializer=[
                numpy_helper.from_array(w_int8, 'wq'),
                numpy_helper.from_array(w_scales, 'ws'),
                numpy_helper.from_array(w_zp, 'wzp'),
            ],
        )
        m_qdq = helper.make_model(g_qdq, opset_imports=[helper.make_opsetid('', 13)])
        ir_qdq = load_onnx_to_ir(m_qdq, quant_config={'input_scale': 1.0, 'input_dtype': 'i4', 'act_scales': {'y': 0.1}})
        # Channel 0 weight=4*0.05=0.20, channel 1 weight=6*0.25=1.50; must reflect true scaled ratio (not raw int8!)
        self.assertEqual(ir_qdq.nodes[0].attrs['weights'], [4, -4, -2, 6])
        self.assertNotEqual(ir_qdq.nodes[1].attrs['shift'][0], ir_qdq.nodes[1].attrs['shift'][1])

        # 2) Gemm default transB=0 (weight shape [K, OC]) + alpha/beta + global final logit scale + Softmax real scale
        w_k_oc = np.array([[0.1, 0.5], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]], dtype=np.float32)  # (K=4, OC=2)
        b_oc = np.array([0.0, 0.0], dtype=np.float32)
        g_gemm = helper.make_graph(
            [
                helper.make_node('Gemm', ['x', 'w', 'b'], ['logits'], alpha=2.0, beta=1.0),  # no transB => default 0!
                helper.make_node('Softmax', ['logits'], ['probs'], axis=1),
            ],
            'gemm_default_transb',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 4])],
            [helper.make_tensor_value_info('probs', TensorProto.FLOAT, [1, 2])],
            initializer=[numpy_helper.from_array(w_k_oc, 'w'), numpy_helper.from_array(b_oc, 'b')],
        )
        m_gemm = helper.make_model(g_gemm, opset_imports=[helper.make_opsetid('', 13)])
        ir_gemm = load_onnx_to_ir(m_gemm, quant_config={'input_scale': 0.25, 'input_dtype': 'u4'})
        self.assertEqual(ir_gemm.nodes[0].attrs['channels'], 2)
        # x=[4,0,0,0] (real=1.0) => logits ≈ [0.2, 1.0] => class 1 must strictly win & Softmax uses real scale!
        x_in = np.array([[[4, 0, 0, 0]]], dtype=np.int64)
        out_probs = oracle_reference(ir_gemm, x_in)['probs'].reshape(-1)
        self.assertEqual(int(np.argmax(out_probs)), 1)
        # With real logit scale restored (0.2 vs 1.0 -> diff 0.8), p[1] ≈ sigmoid(0.8) ≈ 0.69 (NOT 1.0 from unscaled ints!)
        self.assertGreater(float(out_probs[0]), 0.15)
        self.assertLess(float(out_probs[1]), 0.85)
        plan_gemm = compile_graph_to_plan(ir_gemm)
        self.assertTrue(verify_plan(ir_gemm, plan_gemm, x_in)['oracle_vs_tiled_ref_exact'])

        # 3) Padded AveragePool with default count_include_pad=0 MUST fail closed
        g_avg = helper.make_graph(
            [helper.make_node('AveragePool', ['x'], ['y'], kernel_shape=[3, 3], strides=[1, 1], pads=[1, 1, 1, 1])],
            'padded_avg',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 4, 8, 8])],
            [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, 4, 8, 8])],
        )
        m_avg = helper.make_model(g_avg, opset_imports=[helper.make_opsetid('', 13)])
        with self.assertRaises(NotImplementedError):
            load_onnx_to_ir(m_avg)

    def test_11_auto_pad_clip_upper_bound_and_nonzero_qdq_zp(self) -> None:
        # 1) Conv with auto_pad=SAME_UPPER + Clip(0, 6) preserves exact output shape & upper clamp
        w = np.ones((4, 2, 3, 3), dtype=np.float32) * 0.5
        g = helper.make_graph(
            [
                helper.make_node('Conv', ['x', 'w'], ['c'], kernel_shape=[3, 3], strides=[1, 1], auto_pad='SAME_UPPER'),
                helper.make_node('Clip', ['c', 'cmin', 'cmax'], ['y']),
            ],
            'autopad_clip6',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 2, 6, 6])],
            [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, 4, 6, 6])],
            initializer=[
                numpy_helper.from_array(w, 'w'),
                numpy_helper.from_array(np.array(0.0, dtype=np.float32), 'cmin'),
                numpy_helper.from_array(np.array(6.0, dtype=np.float32), 'cmax'),
            ],
        )
        m = helper.make_model(g, opset_imports=[helper.make_opsetid('', 13)])
        # out_scale=1.0 => Clip(0, 6) MUST clamp quantized u4 upper bound to 6 (not 15!)
        ir = load_onnx_to_ir(m, quant_config={'input_scale': 1.0, 'input_dtype': 'u4', 'act_scales': {'y': 1.0}})
        self.assertEqual(ir.nodes[0].attrs['pads'], [1, 1, 1, 1])
        self.assertEqual(ir.nodes[1].attrs['clamp'], [0, 6])
        plan = compile_graph_to_plan(ir)
        x = np.full((6, 6, 2), 10, dtype=np.int64)
        out, _ = TiledRuntime(ReferenceBackend()).run(plan, x)
        self.assertEqual(out.shape, (6, 6, 4))
        self.assertLessEqual(int(out.max()), 6)
        self.assertTrue(verify_plan(ir, plan, x)['oracle_vs_tiled_ref_exact'])

        # 2) Runtime activation QuantizeLinear with non-zero zero_point MUST fail closed
        g_zp = helper.make_graph(
            [
                helper.make_node('QuantizeLinear', ['x', 'sc', 'zp'], ['xq']),
                helper.make_node('DequantizeLinear', ['xq', 'sc', 'zp'], ['y']),
            ],
            'nonzero_zp',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 2, 4, 4])],
            [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, 2, 4, 4])],
            initializer=[
                numpy_helper.from_array(np.array(0.1, dtype=np.float32), 'sc'),
                numpy_helper.from_array(np.array(128, dtype=np.uint8), 'zp'),
            ],
        )
        m_zp = helper.make_model(g_zp, opset_imports=[helper.make_opsetid('', 13)])
        with self.assertRaises(NotImplementedError):
            load_onnx_to_ir(m_zp)

    def test_12_cross_plan_cache_invalidation_pool_attrs_and_reshape_guard(self) -> None:
        # 1) Cross-plan residency cache must NOT skip loading a different plan whose group also has id 'p0000'
        from software.compiler.runtime import NpuBackend

        class _FakeSession:
            def __init__(self) -> None:
                self.loads: int = 0
                self.cur_pkg = None

            def load_model(self, pkg) -> None:
                self.loads += 1
                self.cur_pkg = pkg

            def infer(self, encoded_in: bytes, *, timeout: float = 30.0) -> bytes:
                from software.compiler.ir import abi
                m = self.cur_pkg.manifest
                return abi.encode_tensor([self.loads] * m['output_elements'], m['output_dtype'])

            def read(self, addr: int) -> int:
                return 10

        g1 = GraphIR(
            input=TensorDesc('x', (1, 1, 4), 'u4'),
            nodes=[NodeIR('FC', ['x'], 'y', dict(channels=2, weights=[1, 0, 0, 0, 0, 1, 0, 0], bias=[0, 0]))],
            output='y',
        )
        g2 = GraphIR(
            input=TensorDesc('x', (1, 1, 4), 'u4'),
            nodes=[NodeIR('FC', ['x'], 'y', dict(channels=2, weights=[2, 0, 0, 0, 0, 3, 0, 0], bias=[1, 2]))],
            output='y',
        )
        p1 = compile_graph_to_plan(g1)
        p2 = compile_graph_to_plan(g2)
        self.assertEqual(p1.groups[0].program_id, 'p0000')
        self.assertEqual(p2.groups[0].program_id, 'p0000')
        sess = _FakeSession()
        npu_be = NpuBackend(sess)
        rt = TiledRuntime(npu_be)
        x = np.array([[[1, 2, 3, 4]]], dtype=np.int64)
        out1, _ = rt.run(p1, x)
        out2, _ = rt.run(p2, x)
        self.assertEqual(sess.loads, 2)
        self.assertEqual(int(out1[0, 0, 0]), 1)
        self.assertEqual(int(out2[0, 0, 0]), 2)
        # Re-running p2 immediately SHOULD hit residency cache (no extra load)
        rt.run(p2, x)
        self.assertEqual(sess.loads, 2)

        # 2) Pooling with ceil_mode=1 or dilations=[2, 2] MUST fail closed
        g_ceil = helper.make_graph(
            [helper.make_node('MaxPool', ['x'], ['y'], kernel_shape=[2, 2], strides=[2, 2], ceil_mode=1)],
            'pool_ceil',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 2, 5, 5])],
            [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, 2, 3, 3])],
        )
        with self.assertRaises(NotImplementedError):
            load_onnx_to_ir(helper.make_model(g_ceil, opset_imports=[helper.make_opsetid('', 13)]))

        g_dil = helper.make_graph(
            [helper.make_node('MaxPool', ['x'], ['y'], kernel_shape=[2, 2], strides=[1, 1], dilations=[2, 2])],
            'pool_dil',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 2, 6, 6])],
            [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, 2, 4, 4])],
        )
        with self.assertRaises(NotImplementedError):
            load_onnx_to_ir(helper.make_model(g_dil, opset_imports=[helper.make_opsetid('', 13)]))

        # 3) Non-trivial spatial Reshape before Conv MUST fail closed (cannot silently alias!)
        w_conv = np.ones((2, 2, 1, 1), dtype=np.float32) * 0.5
        g_bad_reshape = helper.make_graph(
            [
                helper.make_node('Reshape', ['x', 'sh'], ['xr']),
                helper.make_node('Conv', ['xr', 'w'], ['y'], kernel_shape=[1, 1], strides=[1, 1], pads=[0, 0, 0, 0]),
            ],
            'bad_spatial_reshape',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 8, 2, 2])],
            [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, 2, 4, 4])],
            initializer=[
                numpy_helper.from_array(np.array([1, 2, 4, 4], dtype=np.int64), 'sh'),
                numpy_helper.from_array(w_conv, 'w'),
            ],
        )
        with self.assertRaises(NotImplementedError):
            load_onnx_to_ir(helper.make_model(g_bad_reshape, opset_imports=[helper.make_opsetid('', 13)]))

        # 4) Valid Flatten(axis=1) -> Gemm on multi-pixel feature map MUST still work & match oracle bit-exactly
        w_fc = np.random.default_rng(12).normal(0.0, 0.3, size=(3, 2 * 2 * 4)).astype(np.float32)
        g_ok_flat = helper.make_graph(
            [
                helper.make_node('Flatten', ['x'], ['xf'], axis=1),
                helper.make_node('Gemm', ['xf', 'w'], ['y'], transB=1),
            ],
            'ok_flatten_fc',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [1, 4, 2, 2])],
            [helper.make_tensor_value_info('y', TensorProto.FLOAT, [1, 3])],
            initializer=[numpy_helper.from_array(w_fc, 'w')],
        )
        ir_ok = load_onnx_to_ir(helper.make_model(g_ok_flat, opset_imports=[helper.make_opsetid('', 13)]),
                                quant_config={'input_scale': 0.25, 'input_dtype': 'u4'})
        plan_ok = compile_graph_to_plan(ir_ok)
        res_ok = verify_plan(ir_ok, plan_ok, np.random.default_rng(13).integers(0, 16, size=(2, 2, 4), dtype=np.int64))
        self.assertTrue(res_ok['oracle_vs_tiled_ref_exact'])


if __name__ == '__main__':
    unittest.main()
