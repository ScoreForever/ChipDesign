"""Host snapshot ownership and performance register bank contract."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from nocpu_jtag import Npu, Op, PERF_NAMES, ABI


class Registers:
    def __init__(self, status=10, signature=0x10018, changed=False):
        self.status, self.signature, self.changed = status, signature, changed
        self.reads = []

    def exchange(self, op, addr=0, data=0):
        assert op == Op.READ, 'Performance reads must not ACK/reset/write'
        self.reads.append(addr)
        if addr == 0: return ABI
        if addr == 0x68: return self.signature
        if addr == 0x2c: return 8 if self.changed and self.reads.count(addr)>1 else 7
        if addr == 4: return self.status
        if 0x100 <= addr < 0x160: return (addr-0x100)//4 + 100
        raise AssertionError(addr)


class PerformanceTests(unittest.TestCase):
    def test_named_bank_and_no_mutating_commands(self):
        registers = Registers()
        result = Npu(registers).performance()
        self.assertEqual(result['job'], 7)
        self.assertEqual([result[name] for name in PERF_NAMES], list(range(100, 124)))
        self.assertEqual([a for a in registers.reads if a >= 0x100], list(range(0x100, 0x160, 4)))

    def test_busy_does_not_read_bank(self):
        registers = Registers(status=3)
        with self.assertRaisesRegex(RuntimeError, 'idle/completed'):
            Npu(registers).performance()
        self.assertFalse(any(a >= 0x100 for a in registers.reads))

    def test_changed_job_is_discarded(self):
        with self.assertRaisesRegex(RuntimeError, 'job changed'):
            Npu(Registers(changed=True)).performance()

    def test_unknown_layout_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'extension v1'):
            Npu(Registers(signature=0x20018)).performance()


if __name__ == '__main__':
    unittest.main()
