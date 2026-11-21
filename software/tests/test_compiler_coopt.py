"""Co-optimization regression checks for ABI 5.0 compiler, runtime, and MPSSE host transport."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
for p in (_ROOT, _ROOT / 'software/host'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import npu_program as abi  # noqa: E402
from nocpu_jtag import FtdiMpsseTransport, Op, create_transport  # noqa: E402
from software.compiler import (  # noqa: E402
    GraphIR,
    NodeIR,
    ReferenceBackend,
    TensorDesc,
    TiledPlan,
    compile_graph_to_plan,
    oracle_reference,
    verify_plan,
)
from software.compiler.ir import requant_np  # noqa: E402
from software.compiler.runtime import TiledPlanRunner, encode_tensor_np, decode_tensor_np, pack_i4_np, unpack_i4_np  # noqa: E402
from software.compiler.tiler import _choose_conv_tiling, _choose_fc_tiling, _Ctx  # noqa: E402
from software.tests.test_mpsse_transport import MockFtdiMpsseTap  # noqa: E402


class ResidencyAndTransportTests(unittest.TestCase):
    def test_repeated_ir_is_not_shifted(self):
        transport = object.__new__(FtdiMpsseTransport)
        transport.ftdi = Mock()
        transport.irscan(0x12)
        transport.irscan(0x12)
        self.assertEqual(transport.ftdi.write_data.call_count, 1)
        transport.irscan(0x13)
        self.assertEqual(transport.ftdi.write_data.call_count, 2)

    def test_close_invalidates_residency(self):
        transport = object.__new__(FtdiMpsseTransport)
        transport.ftdi = Mock()
        transport._cur_ir = 0x12
        transport._cached_io_geometry = (0, 4, 8, 4)
        transport.close()
        self.assertIsNone(transport._cur_ir)
        self.assertIsNone(transport._cached_io_geometry)

    def test_io_geometry_cache_and_invalidation(self):
        dev = MockFtdiMpsseTap(idcode=1, tck_hz=1_000_000, sys_clk_hz=57_143_000)
        tx = FtdiMpsseTransport(ftdi_dev=dev, frequency=1_000_000, sys_clk_hz=57_143_000)
        try:
            read_addrs: list[int] = []
            orig_exchange = tx.exchange

            def spy_exchange(op, addr=0, data=0):
                if op == Op.READ:
                    read_addrs.append(addr)
                if op == Op.START:
                    dev.regs = getattr(dev, '_fake_regs', {})
                return orig_exchange(op, addr, data)

            # Use a lightweight fake exchange layer on an object.__new__ transport to count reg reads.
            ftx = object.__new__(FtdiMpsseTransport)
            ftx.timeout = 5.0
            ftx.batch_rpc = False
            ftx._closed = False
            ftx._cur_ir = 0x12
            ftx._cached_io_geometry = None
            ftx.ftdi = Mock()
            regs = {0x18: 0, 0x1c: 8, 0x20: 16, 0x24: 4, 4: 8}
            seen_reads: list[int] = []

            def fake_exchange(op, addr=0, data=0):
                if op == Op.RESET or op == Op.MODEL_BEGIN:
                    ftx._cached_io_geometry = None
                if op == Op.WRITE and addr < 0x10000:
                    regs[addr] = data
                    return 0
                if op == Op.READ:
                    seen_reads.append(addr)
                    return regs.get(addr, 0x11223344)
                return 0

            ftx.exchange = fake_exchange
            ftx.burst_write = Mock()
            ftx.burst_read = Mock(return_value=[])

            ftx.pipeline_load_model([(0x18, 0), (0x1c, 8), (0x20, 16), (0x24, 4)], [0xaa, 0xbb])
            self.assertEqual(ftx._cached_io_geometry, (0, 8, 16, 4))
            seen_reads.clear()

            # Warm infer must NOT re-read (0x18, 0x1c, 0x20, 0x24).
            with patch('nocpu_jtag.time.sleep') as sleep_mock:
                out, _ = ftx.pipeline_infer([1, 2], 8)
            self.assertEqual(len(out), 4)
            self.assertFalse(any(a in (0x18, 0x1c, 0x20, 0x24) for a in seen_reads))
            sleep_mock.assert_not_called()

            # Reset invalidates IO geometry cache.
            ftx.exchange(Op.RESET)
            self.assertIsNone(ftx._cached_io_geometry)
            seen_reads.clear()
            ftx.pipeline_infer([1, 2], 8)
            for a in (0x18, 0x1c, 0x20, 0x24):
                self.assertIn(a, seen_reads)

            # Adaptive polling skips sleep during the tight 3ms window and sleeps after 3ms.
            poll_count = [0]

            def slow_exchange(op, addr=0, data=0):
                if op == Op.READ and addr == 4:
                    poll_count[0] += 1
                    return 8 if poll_count[0] >= 4 else 0
                return fake_exchange(op, addr, data)

            ftx.exchange = slow_exchange
            mono_vals = iter([0.0, 0.0, 0.001, 0.004, 0.005, 0.006, 0.007])
            with patch('nocpu_jtag.time.monotonic', side_effect=lambda: next(mono_vals, 0.01)), \
                    patch('nocpu_jtag.time.sleep') as sleep_mock:
                ftx.pipeline_infer([1, 2], 8)
            sleep_mock.assert_called_once_with(0.0005)
        finally:
            tx.close()

    def test_sys_clk_mhz_plumbing_in_create_transport(self):
        with patch('nocpu_jtag.FtdiMpsseTransport') as mpsse_cls:
            create_transport('mpsse', ftdi_url='ftdi://ftdi:232h/1', tck_khz=5000, timeout=3.0, sys_clk_mhz=57.143)
            mpsse_cls.assert_called_once_with('ftdi://ftdi:232h/1', 5_000_000, 3.0, sys_clk_hz=57_143_000)

    def test_scan_burst_and_output_failures_invalidate_residency(self):
        def make_tx():
            tx = object.__new__(FtdiMpsseTransport)
            tx.ftdi = Mock()
            tx.timeout = 0.05
            tx.idle_tcks = 8
            tx.write_chunk_words = 512
            tx.read_chunk_words = 256
            tx._cur_ir = 0x12
            tx._cached_io_geometry = (0, 8, 16, 8)
            return tx

        # 1. drscan USB write failure invalidates both caches.
        tx = make_tx()
        tx.ftdi.write_data.side_effect = OSError('usb disconnect')
        with self.assertRaises(OSError):
            tx.drscan(32, 0)
        self.assertIsNone(tx._cur_ir)
        self.assertIsNone(tx._cached_io_geometry)

        # 2. _read timeout invalidates both caches.
        tx = make_tx()
        tx.timeout = 0.0
        tx.ftdi.read_data_bytes.return_value = b''
        with self.assertRaises(TimeoutError):
            tx._read(5)
        self.assertIsNone(tx._cur_ir)
        self.assertIsNone(tx._cached_io_geometry)

        # 3. burst_check overrun/NPU-error invalidates both caches.
        tx = make_tx()
        tx.scan = Mock(return_value=(0x20 << 64))
        with self.assertRaises(RuntimeError):
            tx.burst_check()
        self.assertIsNone(tx._cur_ir)
        self.assertIsNone(tx._cached_io_geometry)

        # 4. burst_read write/read failure invalidates both caches.
        tx = make_tx()
        tx.ftdi.write_data.side_effect = [None, RuntimeError('bulk write failed')]
        with self.assertRaises(RuntimeError):
            tx.burst_read(4)
        self.assertIsNone(tx._cur_ir)
        self.assertIsNone(tx._cached_io_geometry)

        # 5. Output burst_read failure inside pipeline_infer invalidates both caches.
        tx = make_tx()
        tx.exchange = Mock(side_effect=lambda op, addr=0, data=0: 8 if (op == Op.READ and addr == 4) else 0)
        tx.burst_write = Mock()
        tx.burst_read = Mock(side_effect=TimeoutError('output burst failed'))
        with self.assertRaises(TimeoutError):
            tx.pipeline_infer([1, 2], 8, burst=True)
        self.assertIsNone(tx._cur_ir)
        self.assertIsNone(tx._cached_io_geometry)


class VectorizedCodecAndRequantTests(unittest.TestCase):
    def test_pack_unpack_i4_u4_odd_and_i32_roundtrip(self):
        for dt, lo, hi in (('i4', -8, 7), ('u4', 0, 15)):
            rng = np.random.default_rng(123 if dt == 'i4' else 456)
            for n in (1, 2, 7, 15, 64, 127):
                arr = rng.integers(lo, hi + 1, size=n, dtype=np.int64)
                p_np = pack_i4_np(arr, dt)
                p_ref = abi.encode_tensor(arr.tolist(), dt)
                self.assertEqual(p_np, p_ref)
                u_np = unpack_i4_np(p_np, n, dt)
                self.assertEqual(u_np.tolist(), arr.tolist())

        vals_i32 = np.array([-(1 << 31), -17, 0, 42, (1 << 31) - 1], dtype=np.int64)
        b = encode_tensor_np(vals_i32, 'i32')
        self.assertEqual(b, abi.encode_tensor(vals_i32.tolist(), 'i32'))
        self.assertEqual(decode_tensor_np(b, 'i32', len(vals_i32)).tolist(), vals_i32.tolist())

        # Nonzero high padding nibble on odd count must be rejected.
        with self.assertRaises(ValueError):
            unpack_i4_np(b'\x13', 1, 'i4')

    def test_requant_np_matches_scalar_abi_including_q8_carry_and_shift_extremes(self):
        rng = np.random.default_rng(77)
        x = np.array([[[-(1 << 31), -123456, -17, 0, 1, 999999, (1 << 31) - 1, -1]]], dtype=np.int64)
        # Channel 0: mult=32704 triggers q8(mult)==256 -> m=128, shift+1
        # Channel 1: mult=32767 at shift=31 saturates m=255, shift=31
        mults = [32704, 32767, 16384, 1, 0, 24576, 32704, 12345]
        shifts = [-31, 31, -8, 12, -1, 0, 31, -15]
        for dt, lo, hi in (('i4', -8, 7), ('u4', 0, 15)):
            got = requant_np(x, mults, shifts, lo=lo, hi=hi)
            exp = [abi.requant(int(x[0, 0, ch]), mults[ch], shifts[ch], lo, hi) for ch in range(8)]
            self.assertEqual(got.reshape(-1).tolist(), exp)
        big = rng.integers(-(1 << 20), (1 << 20), size=(2, 3, 8), dtype=np.int64)
        got_big = requant_np(big, mults, shifts, lo=-8, hi=7)
        for y in range(2):
            for col in range(3):
                for ch in range(8):
                    self.assertEqual(int(got_big[y, col, ch]), abi.requant(int(big[y, col, ch]), mults[ch], shifts[ch], -8, 7))


class HardwareAddAndFusionTests(unittest.TestCase):
    def _make_add_graph(self, dtype_a='u4', dtype_b='i4', out_dtype='u4', relu=True):
        return GraphIR(
            input=TensorDesc('x', (6, 6, 8), dtype_a),
            nodes=[
                NodeIR('Conv', ['x'], 'c', dict(
                    channels=8, kernel=[1, 1], stride=[1, 1], pads=[0, 0, 0, 0], groups=1,
                    weights=np.random.default_rng(3).integers(-4, 4, size=64, dtype=np.int64).tolist(),
                    bias=[1, -2, 0, 3, -1, 2, 0, -3],
                )),
                NodeIR('Requant', ['c'], 'b', dict(multiplier=[0x4000] * 8, shift=[-4] * 8, clamp=[-8, 7] if dtype_b == 'i4' else [0, 15], dtype=dtype_b)),
                NodeIR('Add', ['x', 'b'], 'add', dict(weights=[3, -2])),
                NodeIR('Requant', ['add'], 'q', dict(multiplier=[0x5000] * 8, shift=[-4] * 8, clamp=[-8, 7] if out_dtype == 'i4' else [0, 15], dtype=out_dtype)),
            ] + ([NodeIR('ReLU', ['q'], 'out', {})] if relu else []),
            output='out' if relu else 'q',
        )

    def test_hardware_add_packages_save_load_and_run_batch(self):
        # 32x32x16 residual block cannot fit as one fused subgraph in 16 KB, so Add+Requant+ReLU uses OP_ADD=7 src2_name tiles.
        rng = np.random.default_rng(11)
        w1 = rng.integers(-4, 4, size=(16, 3, 3, 16), dtype=np.int64).reshape(-1).tolist()
        w2 = rng.integers(-4, 4, size=(16, 3, 3, 16), dtype=np.int64).reshape(-1).tolist()
        graph = GraphIR(
            input=TensorDesc('x', (32, 32, 16), 'u4'),
            nodes=[
                NodeIR('Conv', ['x'], 'c1', dict(channels=16, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1], groups=1, weights=w1, bias=[0] * 16)),
                NodeIR('Requant', ['c1'], 'q1', dict(multiplier=[0x4000] * 16, shift=[-5] * 16, clamp=[0, 15], dtype='u4')),
                NodeIR('Conv', ['q1'], 'c2', dict(channels=16, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1], groups=1, weights=w2, bias=[0] * 16)),
                NodeIR('Requant', ['c2'], 'q2', dict(multiplier=[0x4000] * 16, shift=[-5] * 16, clamp=[-8, 7], dtype='i4')),
                NodeIR('Add', ['x', 'q2'], 'add', dict(weights=[2, 3])),
                NodeIR('Requant', ['add'], 'q3', dict(multiplier=[0x4000] * 16, shift=[-3] * 16, clamp=[0, 15], dtype='u4')),
                NodeIR('ReLU', ['q3'], 'out', {}),
            ],
            output='out',
        )
        plan = compile_graph_to_plan(graph, enable_fusion=True, hardware_add=True)
        add_groups = [g for g in plan.groups if any(c.src2_name is not None for c in g.calls)]
        self.assertGreater(len(add_groups), 0)
        self.assertEqual(plan.summary()['host_post_ops'], 0)

        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / 'plan'
            plan.save(pdir)
            reloaded = TiledPlan.load(pdir)
            self.assertTrue(any(c.src2_name is not None for g in reloaded.groups for c in g.calls))
            frames = [rng.integers(0, 16, size=(32, 32, 16), dtype=np.int64) for _ in range(3)]
            backend = ReferenceBackend()
            results = TiledPlanRunner(backend).run_batch(reloaded, frames)
            self.assertEqual(backend.model_loads, len(reloaded.groups))  # each ProgramGroup loaded ONCE for all 3 frames!
            for f, (out, _tel) in zip(frames, results):
                np.testing.assert_array_equal(out, oracle_reference(graph, f)['out'])

        self.assertEqual(TiledPlanRunner(ReferenceBackend()).run_batch(plan, []), [])

    @unittest.skipUnless(shutil.which('iverilog') and shutil.which('vvp'), 'iverilog/vvp required')
    def test_hardware_add_rtl_bit_exact(self):
        rng = np.random.default_rng(29)
        for da, db, od, relu in (('u4', 'i4', 'u4', True), ('i4', 'i4', 'i4', False), ('u4', 'u4', 'u4', True)):
            a_lo, a_hi = (0, 16) if da == 'u4' else (-8, 8)
            b_lo, b_hi = (0, 16) if db == 'u4' else (-8, 8)
            from software.compiler.tiler import _Ctx, _tile_add_layer
            tensors = {
                'a': TensorDesc('a', (4, 5, 8), da),
                'b': TensorDesc('b', (4, 5, 8), db),
                'add': TensorDesc('add', (4, 5, 8), 'i32'),
                'q': TensorDesc('q', (4, 5, 8), od),
            }
            if relu:
                tensors['out'] = TensorDesc('out', (4, 5, 8), od)
            req_n = NodeIR('Requant', ['add'], 'q', dict(multiplier=[0x4800] * 8, shift=[-3] * 8, clamp=[0, 15] if od == 'u4' else [-8, 7], dtype=od))
            relu_n = NodeIR('ReLU', ['q'], 'out', {}) if relu else None
            add_n = NodeIR('Add', ['a', 'b'], 'add', dict(weights=[2, -3]))
            groups = _tile_add_layer(add_n, req_n, relu_n, tensors, _Ctx(8, 8192, 8192, 128, [0]))
            self.assertIsNotNone(groups)
            a_arr = rng.integers(a_lo, a_hi, size=(4, 5, 8), dtype=np.int64)
            b_arr = rng.integers(b_lo, b_hi, size=(4, 5, 8), dtype=np.int64)
            from software.compiler.runtime import RtlBackend
            rtl = RtlBackend()
            try:
                out_rtl = rtl.infer_tile(groups[0], a_arr, b_arr)
                self.assertGreater(rtl.npu_cycles, 0)
            finally:
                rtl.close()
            out_ref = ReferenceBackend().infer_tile(groups[0], a_arr, b_arr)
            np.testing.assert_array_equal(out_rtl, out_ref)

    def test_conv_maxpool_halo_fusion_with_padded_borders_exact(self):
        # Asymmetric / padded case: 11x12x3 -> Conv 3x3 s2 p1 (6x6x16) -> MaxPool 3x3 s2 p=[1,1,1,1] (3x3x16)
        rng = np.random.default_rng(42)
        w = rng.integers(-6, 7, size=(16, 3, 3, 3), dtype=np.int64).reshape(-1).tolist()
        graph = GraphIR(
            input=TensorDesc('x', (15, 15, 3), 'i4'),
            nodes=[
                NodeIR('Conv', ['x'], 'c', dict(channels=16, kernel=[3, 3], stride=[2, 2], pads=[1, 1, 1, 1], groups=1, weights=w, bias=[2] * 16)),
                NodeIR('Requant', ['c'], 'q', dict(multiplier=[0x5000] * 16, shift=[-5] * 16, clamp=[0, 15], dtype='u4')),
                NodeIR('ReLU', ['q'], 'r', {}),
                NodeIR('MaxPool', ['r'], 'p', dict(kernel=[3, 3], stride=[2, 2], pads=[1, 1, 1, 1])),
            ],
            output='p',
        )
        # Larger input (65x65x3 -> 33x33x32 -> 17x17x32) guarantees fused interior + padded border strips!
        w2 = rng.integers(-6, 7, size=(32, 3, 3, 3), dtype=np.int64).reshape(-1).tolist()
        big_graph = GraphIR(
            input=TensorDesc('x', (65, 65, 3), 'i4'),
            nodes=[
                NodeIR('Conv', ['x'], 'c', dict(channels=32, kernel=[3, 3], stride=[2, 2], pads=[1, 1, 1, 1], groups=1, weights=w2, bias=[1] * 32)),
                NodeIR('Requant', ['c'], 'q', dict(multiplier=[0x5000] * 32, shift=[-5] * 32, clamp=[0, 15], dtype='u4')),
                NodeIR('ReLU', ['q'], 'r', {}),
                NodeIR('MaxPool', ['r'], 'p', dict(kernel=[3, 3], stride=[2, 2], pads=[1, 1, 1, 1])),
            ],
            output='p',
        )
        plan = compile_graph_to_plan(big_graph, enable_fusion=True)
        self.assertGreater(plan.summary()['fused_subgraphs'], 0)
        x = rng.integers(-8, 8, size=(65, 65, 3), dtype=np.int64)
        res = verify_plan(big_graph, plan, x)
        self.assertTrue(res['oracle_vs_tiled_ref_exact'])

    def test_joint_conv_and_fc_tiling_beats_naive_112(self):
        # 28x28x128 -> 28x28x128: joint search must consider smaller ic_tile (e.g. 64) to slash calls/bytes vs ic_t=112!
        ic_t, oc_t, oh_t, ow_t, k_tiled = _choose_conv_tiling(
            28, 28, 128, 128, 3, 3, 1, 1, 28, 28, False, 'u4', 'u4', cols=8, act_bytes=8192, model_bytes=8192,
        )
        self.assertTrue(k_tiled)
        self.assertEqual(ic_t, 64)
        # Joint FC search on 512 -> 1000 picks smaller k_tile with wide oc_tile so groups drop below 42 (k=512, oc=24).
        import math
        _, fc_k, fc_oc = _choose_fc_tiling(512, 1000, 'u4', 'i32', _Ctx(8, 8192, 8192, 128, [0]))
        groups = math.ceil(512 / fc_k) * math.ceil(1000 / fc_oc)
        self.assertLess(groups, 42)
        self.assertGreaterEqual(fc_oc, 48)


if __name__ == '__main__':
    unittest.main()
