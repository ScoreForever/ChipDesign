"""Check timing around the real host driver, with a recording hardware oracle."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'software/host'), str(ROOT/'hardware/npu/scripts')]
from npu_e2e import PhaseTimer, TimedTransport, measure
from nocpu_jtag import Npu, Op
from npu_program import compile_model
from run_program_regression import Recorder

spec = importlib.util.spec_from_file_location('board_timing', ROOT/'verification/fpga/board/nocpu_e2e_timing.py')
board = importlib.util.module_from_spec(spec); spec.loader.exec_module(board)


class TimingTests(unittest.TestCase):
    def test_spans_reconcile_and_include_host_gaps(self):
        ticks = iter([0, 10, 30, 80])
        timer = PhaseTimer(lambda: next(ticks))
        timer.begin('upload'); timer.mark('commit')
        result = timer.finish()
        self.assertEqual(result['total_wall_ms'], 80/1e6)
        self.assertEqual([x['wall_ms'] for x in result['phases']], [30/1e6, 50/1e6])

    def fixture(self):
        pkg = compile_model(dict(input=dict(name='x', shape=[1, 1, 2]), nodes=[
            dict(op='FC', input='x', output='y', channels=1, weights=[1, 2], bias=[0])]), cols=8)
        expected = (5).to_bytes(4, 'little')
        rec = Recorder(pkg, expected)
        rec.regs.update({0x68: 0x10018, 0x100: 500})
        timer = PhaseTimer()
        npu = Npu(TimedTransport(rec, timer))
        return pkg, expected, rec, timer, npu

    def test_real_driver_model_residency_phases_and_counters(self):
        pkg, expected, rec, timer, npu = self.fixture()
        with tempfile.TemporaryDirectory() as d:
            record = {}
            board.benchmark(npu, timer, pkg, b'\x21', expected, 2, 50, Path(d), record)
            self.assertTrue(all(r['matches_expected'] for r in record['runs']))
            self.assertEqual(record['runs'][0]['compute_ms_from_cycles'], .01)
            phases = [p['phase'] for p in record['runs'][0]['timing']['phases']]
            self.assertEqual(phases, ['input_metadata', 'input_upload', 'input_commit', 'start',
                                     'completion_poll', 'output_metadata', 'output_readout', 'ack'])
            self.assertEqual(sum(row.startswith('03 ') for row in rec.rows), 1)
            self.assertEqual(sum(row.startswith('07 ') for row in rec.rows), 2)
            self.assertTrue((Path(d)/'actual_0001.bin').exists())

    def test_mismatch_preserves_output_and_stops_repeats(self):
        pkg, expected, rec, timer, npu = self.fixture()
        with tempfile.TemporaryDirectory() as d:
            record = {}
            with self.assertRaisesRegex(RuntimeError, 'golden mismatch'):
                board.benchmark(npu, timer, pkg, b'\x21', b'\xff'*4, 3, 50, Path(d), record)
            self.assertEqual((Path(d)/'actual_0000.bin').read_bytes(), expected)
            self.assertEqual(len(record['runs']), 1)
            self.assertFalse(record['runs'][0]['matches_expected'])

    def test_failed_start_is_not_retried_or_reset(self):
        class Broken:
            calls = []
            def exchange(self, op, addr=0, data=0):
                self.calls.append(op)
                raise TimeoutError('unknown outcome')
        raw = Broken(); timer = PhaseTimer(); tx = TimedTransport(raw, timer)
        with self.assertRaisesRegex(TimeoutError, 'unknown outcome'):
            measure(timer, 'input_metadata', lambda: tx.exchange(Op.START))
        self.assertEqual(raw.calls, [Op.START])
        self.assertEqual(timer.failed['phases'][1]['phase'], 'start')

    def test_wordwise_delegation_does_not_expose_bursts(self):
        class Burst:
            def burst_write(self, words): pass
        timer = PhaseTimer()
        self.assertTrue(hasattr(TimedTransport(Burst(), timer), 'burst_write'))
        self.assertFalse(hasattr(TimedTransport(Burst(), timer, True), 'burst_write'))


if __name__ == '__main__':
    unittest.main()
