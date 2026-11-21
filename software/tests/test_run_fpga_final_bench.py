"""End-to-end checks of run_fpga_final_bench with the in-process mock NPU."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / 'software/host', ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import run_fpga_final_bench as bench  # noqa: E402
from nocpu_jtag import Npu  # noqa: E402

try:
    from software.compiler import GraphIR, NodeIR, TensorDesc, compile_graph_to_plan  # noqa: E402
    COMPILER_ERROR = None
except ImportError as exc:  # compiler package mid-edit
    COMPILER_ERROR = f'software.compiler unavailable: {exc}'


def run_main(*argv):
    out = Path(tempfile.mkdtemp(prefix='fb_test_'))
    code = bench.main(['--out-dir', str(out), *argv])
    load = lambda name: json.loads((out / f'{name}.json').read_text())  # noqa: E731
    return code, load


def tiny_plan(directory):
    rng = np.random.default_rng(1)
    w1 = rng.integers(-4, 4, size=(8, 3, 3, 4)).reshape(-1).tolist()
    w2 = rng.integers(-4, 4, size=(4, 3, 3, 8)).reshape(-1).tolist()
    w_fc = rng.integers(-4, 4, size=(5, 4 * 6 * 6)).reshape(-1).tolist()
    graph = GraphIR(
        input=TensorDesc('x', (6, 6, 4), 'i4'),
        nodes=[
            NodeIR('Conv', ['x'], 'c1', dict(channels=8, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1],
                                            groups=1, weights=w1, bias=[1] * 8)),
            NodeIR('Requant', ['c1'], 'q1', dict(multiplier=[0x4000] * 8, shift=[-4] * 8, clamp=[-8, 7], dtype='i4')),
            NodeIR('ReLU', ['q1'], 'r1', {}),
            NodeIR('Conv', ['r1'], 'c2', dict(channels=4, kernel=[3, 3], stride=[1, 1], pads=[1, 1, 1, 1],
                                             groups=1, weights=w2, bias=[0] * 4)),
            NodeIR('Requant', ['c2'], 'q2', dict(multiplier=[0x4000] * 4, shift=[-4] * 4, clamp=[-8, 7], dtype='i4')),
            NodeIR('Add', ['x', 'q2'], 'add', dict(weights=[1, 1])),
            NodeIR('Requant', ['add'], 'q3', dict(multiplier=[0x4000] * 4, shift=[-1] * 4, clamp=[-8, 7], dtype='i4')),
            NodeIR('FC', ['q3'], 'logits', dict(channels=5, weights=w_fc, bias=[0, 1, -1, 2, 3])),
        ],
        output='logits',
    )
    compile_graph_to_plan(graph).save(directory)
    samples = Path(directory) / 'samples.npz'
    np.savez(samples, inputs=rng.integers(-8, 8, size=(2, 6, 6, 4)).astype(np.int8), labels=np.array([0, 1]))
    return samples


class MockTransport(unittest.TestCase):
    def test_mock_runs_package_through_driver(self):
        package, model, golden = bench.load_zoo_model('ecg')
        transport = bench.MockNpuTransport()
        transport.register(package, model)
        npu = Npu(transport)
        npu.load_model(package)
        frame = golden['frames'][0]
        self.assertEqual(list(npu.infer_values(frame['input'])), frame['output'])
        self.assertEqual(npu.read(0x38), 0)

    def test_unregistered_image_is_rejected(self):
        package, _, _ = bench.load_zoo_model('har')
        with self.assertRaisesRegex(RuntimeError, 'not registered'):
            Npu(bench.MockNpuTransport()).load_model(package)


class Suites(unittest.TestCase):
    def test_models_mock(self):
        code, load = run_main('--backend', 'mock', '--suite', 'models', '--max-frames', '2')
        self.assertEqual(code, 0)
        result = load('models')
        self.assertTrue(result['passed'])
        self.assertFalse(result['fpga_available'])
        self.assertFalse(result['fpga_validated'])
        rows = result['runs'][0]['models']
        self.assertEqual(sorted(rows), sorted(bench.MODELS))
        for row in rows.values():
            self.assertTrue(row['golden_match'])
            self.assertIsNone(row['npu_cycles'])
            self.assertIsNone(row['cold']['npu_cycles'])
        self.assertFalse(load('summary')['fpga_validated'])

    def test_scenes_mock_receipt(self):
        code, load = run_main('--backend', 'mock', '--suite', 'scenes', '--max-frames', '1')
        self.assertEqual(code, 0)
        receipt = load('scenes')
        self.assertFalse(receipt['fpga_validated'])
        self.assertFalse(receipt['fpga_available'])
        self.assertEqual(receipt['real_board_frames'], 0)
        self.assertEqual(receipt['frames_checked'], 6)
        self.assertEqual(receipt['agreement_rate'], 1.0)
        self.assertIn('80/80', receipt['scope_note'])
        self.assertIn('112/112', receipt['scope_note'])
        self.assertEqual(sorted(receipt['runs'][0]['scenes']), sorted(bench.SCENES))

    def test_mismatch_gives_nonzero_exit(self):
        corrupt = lambda: bench.MockNpuTransport(corrupt_outputs=True)  # noqa: E731
        with patch.object(bench, 'make_mock_transport', corrupt):
            code, load = run_main('--backend', 'mock', '--suite', 'models', '--max-frames', '1')
        self.assertNotEqual(code, 0)
        self.assertFalse(load('models')['passed'])
        self.assertFalse(load('summary')['passed'])

    def test_missing_resnet_plans_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            code, load = run_main('--backend', 'mock', '--suite', 'resnet',
                                  '--resnet20-plan', f'{td}/none20', '--resnet18-plan', f'{td}/none18')
        self.assertEqual(code, 0)
        result = load('resnet')
        self.assertEqual(len(result['skipped']), 2)
        self.assertTrue(all('missing' in s for s in result['skipped']))
        self.assertFalse(result['fpga_validated'])

    @unittest.skipIf(COMPILER_ERROR, COMPILER_ERROR or '')
    def test_resnet_tiny_plan_mock(self):
        with tempfile.TemporaryDirectory() as td:
            samples = tiny_plan(td)
            code, load = run_main('--backend', 'mock', '--suite', 'resnet',
                                  '--resnet20-plan', td, '--resnet20-samples', str(samples),
                                  '--resnet18-plan', f'{td}/missing')
            result = load('resnet')
        self.assertEqual(code, 0, result)
        row = result['runs'][0]['models']['resnet20']
        self.assertTrue(row['bit_exact'])
        self.assertEqual(row['samples_checked'], 2)
        self.assertIsNone(row['npu_cycles_median'])
        self.assertIsNone(row['rows'][0]['npu_compute_ms'])
        self.assertGreater(row['rows'][0]['telemetry']['tile_infers'], 0)
        self.assertEqual(len(result['skipped']), 1)
        self.assertFalse(result['fpga_validated'])

    def test_sys_clk_and_tck_reach_create_transport(self):
        calls = []

        def fake_create(backend, **kwargs):
            calls.append((backend, kwargs))
            transport = bench.MockNpuTransport()
            return transport, transport.close
        with patch.object(bench, 'create_transport', fake_create):
            code, load = run_main('--backend', 'mpsse', '--suite', 'models', '--max-frames', '1',
                                  '--tck-mhz-list', '2,5', '--sys-clk-mhz', '42.5')
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in calls], ['mpsse', 'mpsse'])
        self.assertEqual([c[1]['tck_khz'] for c in calls], [2000, 5000])
        self.assertTrue(all(c[1]['sys_clk_mhz'] == 42.5 for c in calls))
        result = load('models')
        self.assertEqual([r['label'] for r in result['runs']], ['tck_2mhz', 'tck_5mhz'])
        # A mock transport behind a hardware backend must never claim board validation.
        self.assertFalse(result['fpga_validated'])
        self.assertFalse(result['fpga_available'])

    def test_openocd_single_run(self):
        calls = []

        def fake_create(backend, **kwargs):
            calls.append((backend, kwargs))
            transport = bench.MockNpuTransport()
            return transport, transport.close
        with patch.object(bench, 'create_transport', fake_create):
            code, load = run_main('--backend', 'openocd', '--suite', 'models', '--max-frames', '1')
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('tck_khz', calls[0][1])
        self.assertIn('OpenOCD', load('models')['runs'][0]['tck_note'])

    def test_unreachable_board_fails(self):
        def refuse(backend, **kwargs):
            raise OSError('no adapter')
        with patch.object(bench, 'create_transport', refuse):
            code, load = run_main('--backend', 'mpsse', '--suite', 'scenes', '--tck-mhz-list', '1')
        self.assertNotEqual(code, 0)
        self.assertFalse(load('scenes')['fpga_validated'])


if __name__ == '__main__':
    unittest.main()
