"""MPSSE wire encoding and dual-clock CDC TAP emulator tests (no USB hardware required)."""
import importlib.util
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from nocpu_jtag import FtdiMpsseTransport, _ensure_libusb_backend


class MpsseEncodingTests(unittest.TestCase):
    def test_last_bit_is_sampled_with_tms(self):
        transport = object.__new__(FtdiMpsseTransport)
        wire = transport._encode_scan(72, 1 << 71)
        self.assertEqual(wire[-6:-3], bytes((0x6b, 0, 0x81)))
        self.assertEqual(wire[:3], bytes((0x4b, 2, 1)))

    def test_decode_top_bit_and_byte_boundary(self):
        for value in (0, 1, 1 << 64, 1 << 71, (1 << 72) - 1):
            raw = (value & ((1 << 64) - 1)).to_bytes(8, 'little')
            raw += bytes((((value >> 64) & 127) << 1, ((value >> 71) & 1) << 7))
            self.assertEqual(FtdiMpsseTransport._decode(raw, 72), value)

    def test_write_scan_has_no_read_commands(self):
        transport = object.__new__(FtdiMpsseTransport)
        wire = transport._encode_scan(32, 0x01234567, False)
        # FTDI AN_108: 0x19 = bytes out(-ve) LSB first, 0x1b = bits out(-ve) LSB first
        self.assertEqual(wire[3], 0x19)
        self.assertEqual(wire[9], 0x1b)

    def test_an108_lsb_first_opcodes_and_asymmetric_ir(self):
        transport = object.__new__(FtdiMpsseTransport)
        # 5-bit IR=0x12 (binary 10010): low 4 bits = 0x2 via 0x1b (LSB-first), MSB=1 via 0x4b
        ir_wire = transport._encode_scan(5, 0x12, read=False, ir=True)
        self.assertEqual(ir_wire[3:6], bytes((0x1b, 3, 0x02)))
        self.assertEqual(ir_wire[6:9], bytes((0x4b, 0, 0x81)))
        # 5-bit IR=0x01 (IDCODE, binary 00001): low 4 bits = 0x1 via 0x1b, MSB=0 via 0x4b
        id_wire = transport._encode_scan(5, 0x01, read=False, ir=True)
        self.assertEqual(id_wire[3:6], bytes((0x1b, 3, 0x01)))
        self.assertEqual(id_wire[6:9], bytes((0x4b, 0, 0x01)))
        # 32-bit read/write DR scan must use 0x39 (LSB-first bytes) & 0x3b (LSB-first bits)
        dr_wire = transport._encode_scan(32, 0x01234567, read=True)
        self.assertEqual(dr_wire[3], 0x39)
        self.assertEqual(dr_wire[9], 0x3b)


class MockFtdiMpsseTap:
    """Behavioral dual-clock (TCK vs scheduled sys_clk delay) TAP + AN_108 MPSSE emulator.

    Models IEEE 1149.1 TAP transitions, AN_108 bit-3 (LSB vs MSB first) shift
    semantics, and a fixed-delay sys_clk mailbox response crossing a 2-flop
    ack_sync chain in the TCK domain.
    """
    NEXT = ((1, 0), (1, 2), (3, 9), (4, 5), (4, 5), (6, 8), (6, 7), (4, 8),
            (1, 2), (10, 0), (11, 12), (11, 12), (13, 15), (13, 14), (11, 15), (1, 2))

    def __init__(self, idcode=1, *, tck_hz=1_000_000, sys_clk_hz=50_000_000, sys_roundtrip_cycles=6):
        self.idcode = idcode
        self.tck_period_ns = 1e9 / float(tck_hz)
        self.sys_period_ns = 1e9 / float(sys_clk_hz)
        self.sys_roundtrip_ns = sys_roundtrip_cycles * self.sys_period_ns
        self.time_ns = 0.0
        self.tap = 0
        self.ir = self.ir_shift = 1
        self.dr = self.packet = self.reply = self.error = 0
        self.pending = self.valid = self.overrun = False
        self.req_toggle = 0
        self.ack_toggle = 0
        self.ack_sync = [0, 0]
        self.ack_ready_ns = None
        self.staged_reply = 0
        self.staged_error = 0
        self.burst = False
        self.burst_error_pending = False
        self.word = 0
        self.rx = bytearray()
        self.writes = []
        self.closed = False
        self.regs = {0: 0x50000, 0x50: 8192, 0x54: 8192, 0x58: 0x10008,
                     0x5c: 0, 0x60: 128, 0x68: 0x10018, 0x6c: 1}
        self.memory = {}
        self.requests = []
        self.idle_runs = []
        self.idles = 0
        self.freeze = False
        self.force_error = 0
        self.address_xor = 0
        self.fail_write_after = None
        self.partial_read_limit = None

    def execute(self, op, addr, data):
        self.requests.append((op, addr, data))
        if op == 2:
            (self.memory if addr >= 0x10000 else self.regs)[addr] = data
        elif op == 7:
            self.regs[4] = 8
            self.memory[0x20000 + self.regs[0x20]] = 5
        elif op == 8:
            self.regs[4] = 2
        return (self.memory if addr >= 0x10000 else self.regs).get(addr, 0)

    def clock(self, tms, tdi):
        state = self.tap
        tdo = (self.ir_shift if state == 11 else self.dr) & 1

        # Advance independent sys_clk response before this TCK rising edge
        self.time_ns += self.tck_period_ns
        if self.ack_ready_ns is not None and not self.freeze and self.time_ns >= self.ack_ready_ns:
            self.staged_reply = self.execute(
                self.packet >> 64, (self.packet >> 32) & 0xffffffff, self.packet & 0xffffffff
            )
            self.staged_error = self.force_error
            self.ack_toggle = self.req_toggle
            self.ack_ready_ns = None

        # Pre-edge values (matching Verilog nonblocking assignments in npu_jtag_transport.sv)
        pending = self.pending
        valid = self.valid
        reply = self.reply
        error = self.error
        overrun = self.overrun
        burst_err_pend = self.burst_error_pending
        old_ack_sync0, old_ack_sync1 = self.ack_sync

        # Two-flop ack_sync update in TCK domain
        self.ack_sync = [self.ack_toggle, old_ack_sync0]
        self.burst_error_pending = False
        if burst_err_pend and error != 0:
            self.overrun = True
        if pending and old_ack_sync1 == self.req_toggle:
            self.pending = False
            self.valid = True
            self.reply = self.staged_reply
            self.error = self.staged_error
            self.burst_error_pending = self.burst

        if state == 0:
            self.ir, self.overrun, self.burst_error_pending = 1, False, False
        elif state == 10:
            self.ir_shift = 5
        elif state == 11:
            self.ir_shift = (self.ir_shift >> 1) | (tdi << 4)
        elif state == 15:
            self.ir = self.ir_shift
        elif state == 3:
            if self.ir == 1:
                self.dr = self.idcode
            elif self.ir == 0x12:
                addr = ((self.packet >> 32) & 0xffffffff) ^ self.address_xor
                ov = overrun or (burst_err_pend and error != 0)
                self.dr = (reply | (addr << 32) | (error << 64)
                           | (int(ov) << 69) | (int(pending) << 70) | (int(valid) << 71))
            elif self.ir == 0x14:
                self.dr = reply
            else:
                self.dr = 0
        elif state == 4:
            width = 72 if self.ir == 0x12 else 32 if self.ir in (1, 0x13, 0x14) else 1
            self.dr = (self.dr >> 1) | (tdi << (width - 1))
        elif state == 8:
            packet = self.dr
            burst = self.ir in (0x13, 0x14)
            if burst:
                packet = ((2 if self.ir == 0x13 else 1) << 64) | (self.word << 32) | (self.dr if self.ir == 0x13 else 0)
            if burst or (self.ir == 0x12 and packet >> 64):
                if pending:
                    self.overrun = True
                else:
                    self.packet = packet
                    self.word = ((packet >> 32) & 0xffffffff) + 4
                    self.req_toggle ^= 1
                    self.ack_ready_ns = self.time_ns + self.sys_roundtrip_ns
                    self.pending, self.valid, self.burst = True, False, burst
                    if not burst:
                        self.overrun = False

        if state == 1 and not tms:
            self.idles += 1
        elif self.idles:
            self.idle_runs.append(self.idles)
            self.idles = 0
        self.tap = self.NEXT[state][tms]
        return tdo

    def write_data(self, buf):
        if self.fail_write_after is not None and len(self.writes) >= self.fail_write_after:
            raise OSError('simulated USB disconnect')
        self.writes.append(bytes(buf))
        i = 0
        while i < len(buf):
            cmd = buf[i]; i += 1
            if cmd in (0x80, 0x86):
                i += 2
            elif cmd in (0x8a, 0x8b, 0x85, 0x87):
                pass
            elif cmd in (0x11, 0x19, 0x31, 0x39):
                # FTDI AN_108 bit 3 (0x08): 1 = LSB first, 0 = MSB first
                lsb_first = bool(cmd & 0x08)
                do_read = bool(cmd & 0x20)
                n = 1 + buf[i] + 256 * buf[i + 1]; i += 2
                for value in buf[i:i + n]:
                    result = 0
                    for b in range(8):
                        tdi = ((value >> b) & 1) if lsb_first else ((value >> (7 - b)) & 1)
                        tdo = self.clock(0, tdi)
                        if lsb_first:
                            result = (result >> 1) | (tdo << 7)
                        else:
                            result = ((result << 1) | tdo) & 0xff
                    if do_read:
                        self.rx.append(result)
                i += n
            elif cmd in (0x13, 0x1b, 0x33, 0x3b, 0x4b, 0x6b):
                lsb_first = bool(cmd & 0x08)
                is_tms = bool(cmd & 0x40)
                do_read = bool(cmd & 0x20)
                n, value = buf[i] + 1, buf[i + 1]; i += 2
                result = 0
                for b in range(n):
                    if is_tms:
                        tms = (value >> b) & 1
                        tdi = (value >> 7) & 1
                    else:
                        tms = 0
                        tdi = ((value >> b) & 1) if lsb_first else ((value >> (7 - b)) & 1)
                    tdo = self.clock(tms, tdi)
                    if lsb_first or is_tms:
                        result = (result >> 1) | (tdo << 7)
                    else:
                        result = ((result << 1) | tdo) & 0xff
                if do_read:
                    self.rx.append(result)
            else:
                raise AssertionError(f'unsupported MPSSE command {cmd:#x}')
        return len(buf)

    def read_data_bytes(self, n, attempt=1):
        if self.partial_read_limit is not None:
            take = min(n, self.partial_read_limit, len(self.rx))
            self.partial_read_limit -= take
        else:
            take = min(n, len(self.rx))
        result = self.rx[:take]
        del self.rx[:take]
        return result

    def close(self):
        self.closed = True


class TapTests(unittest.TestCase):
    def make(self, *, frequency=1_000_000, sys_clk_hz=50_000_000, idcode=1, **kwargs):
        dev = MockFtdiMpsseTap(idcode=idcode, tck_hz=frequency, sys_clk_hz=sys_clk_hz)
        return dev, FtdiMpsseTransport(ftdi_dev=dev, frequency=frequency, sys_clk_hz=sys_clk_hz, **kwargs)

    def test_idcode_and_init_failure_closes_device(self):
        self.make()
        for code in (0, 0xffffffff):
            dev = MockFtdiMpsseTap(idcode=code)
            with self.assertRaisesRegex(RuntimeError, 'IDCODE'):
                FtdiMpsseTransport(ftdi_dev=dev)
            self.assertTrue(dev.closed)
        # Initial write_data failure must ALSO close the opened FTDI device (no USB handle leak)
        dev = MockFtdiMpsseTap()
        dev.fail_write_after = 0
        with self.assertRaises(OSError):
            FtdiMpsseTransport(ftdi_dev=dev)
        self.assertTrue(dev.closed)

    @unittest.skipUnless(
        importlib.util.find_spec('pyftdi') and importlib.util.find_spec('usb') and importlib.util.find_spec('libusb_package'),
        'optional pyftdi/pyusb/libusb_package not installed',
    )
    def test_libusb_package_backend_resolution_works_with_pyftdi(self):
        import usb.backend.libusb1
        from pyftdi.usbtools import UsbTools
        orig = usb.backend.libusb1.get_backend
        fake_backend = object()

        def fake_get_backend(find_library=None):
            return fake_backend if find_library is not None else None

        try:
            usb.backend.libusb1.get_backend = fake_get_backend
            _ensure_libusb_backend()
            # UsbTools._load_backend() expects module-name strings and calls mod.get_backend()
            self.assertIs(UsbTools._load_backend(), fake_backend)
        finally:
            usb.backend.libusb1.get_backend = orig

    def test_mailbox_order(self):
        from nocpu_jtag import Op
        dev, tx = self.make()
        for bit in (0, 31, 32, 63, 64, 71):
            dev.reply = (1 << bit) & 0xffffffff
            dev.packet = (1 << bit) & (0xffffffff << 32)
            dev.error = ((1 << bit) >> 64) & 31
            dev.valid = bit == 71
            self.assertEqual(tx.scan(0), 1 << bit)
        for value in (1, 0x80000000, 0xdeadbeef):
            tx.exchange(Op.WRITE, 0x12345678, value)
            self.assertEqual(tx.exchange(Op.READ, 0x12345678), value)

    def test_bulk_dual_clock_cdc_and_overrun_enforcement(self):
        from nocpu_jtag import Op
        for hz, idle in ((1_000_000, 5), (10_000_000, 9), (15_000_000, 12), (30_000_000, 19)):
            with self.subTest(tck_hz=hz):
                dev, tx = self.make(frequency=hz)
                self.assertEqual(tx.idle_tcks, idle)
                words = [(i * 0x1234567) & 0xffffffff for i in range(201)]
                tx.exchange(Op.WRITE, 0x20000, words[0])
                before = len(dev.writes)
                tx.burst_write(words[1:])
                self.assertLessEqual(len(dev.writes) - before, 4)
                self.assertEqual([dev.memory[0x20000 + 4 * i] for i in range(201)], words)
                self.assertEqual(tx.exchange(Op.READ, 0x20000), words[0])
                before = len(dev.writes)
                self.assertEqual(tx.burst_read(100), words[1:101])
                self.assertLessEqual(len(dev.writes) - before, 5)
                self.assertIn(idle, dev.idle_runs)
                self.assertFalse(dev.overrun)

        # High-TCK worst-case latency stress (30 MHz TCK, 50 MHz sys_clk, 14- and 24-cycle response):
        # default burst_read_sys_cycles=24 must capture EVERY multi-word read cleanly with zero stale words!
        for latency in (14, 24):
            with self.subTest(stress_latency=latency):
                dev_hi = MockFtdiMpsseTap(idcode=1, tck_hz=30_000_000, sys_clk_hz=50_000_000, sys_roundtrip_cycles=latency)
                tx_hi = FtdiMpsseTransport(ftdi_dev=dev_hi, frequency=30_000_000, sys_clk_hz=50_000_000)
                expected = [100 + i for i in range(17)]
                for i, w in enumerate(expected):
                    dev_hi.memory[0x20000 + 4 * i] = w
                self.assertEqual(tx_hi.exchange(Op.READ, 0x20000), expected[0])
                self.assertEqual(tx_hi.burst_read(16), expected[1:])
                self.assertFalse(dev_hi.overrun)

        # Negative CDC test: at 30 MHz with 18-cycle latency, under-budgeting burst_read_sys_cycles=8
        # triggers stale capture, confirming the emulator enforces the worst-case bound!
        dev_bad = MockFtdiMpsseTap(idcode=1, tck_hz=30_000_000, sys_clk_hz=50_000_000, sys_roundtrip_cycles=18)
        tx_bad = FtdiMpsseTransport(ftdi_dev=dev_bad, frequency=30_000_000, sys_clk_hz=50_000_000, burst_read_sys_cycles=8)
        for i in range(8):
            dev_bad.memory[0x20000 + 4 * i] = 100 + i
        tx_bad.exchange(Op.READ, 0x20000)
        got = tx_bad.burst_read(6)
        self.assertNotEqual(got, [101, 102, 103, 104, 105, 106])

        dev_bad.overrun = True
        with self.assertRaisesRegex(RuntimeError, 'overrun'):
            tx_bad.burst_check()

    def test_burst_check_polls_until_valid_and_times_out_when_frozen(self):
        from nocpu_jtag import Op
        # 1) Frozen final burst read MUST time out instead of returning stale [0]
        dev_frozen, tx_frozen = self.make(frequency=10_000_000, timeout=0.005)
        dev_frozen.memory[0x20000] = 0x11111111
        dev_frozen.memory[0x20004] = 0x22222222
        self.assertEqual(tx_frozen.exchange(Op.READ, 0x20000), 0x11111111)
        dev_frozen.freeze = True
        with self.assertRaisesRegex(TimeoutError, 'burst completion timed out'):
            tx_frozen.burst_read(1)
        self.assertTrue(dev_frozen.pending)

        # 2) Delayed final burst response (slow sys_clk) polls without resubmitting and captures exact final word
        dev_slow = MockFtdiMpsseTap(idcode=1, tck_hz=10_000_000, sys_clk_hz=50_000_000, sys_roundtrip_cycles=80)
        tx_slow = FtdiMpsseTransport(ftdi_dev=dev_slow, frequency=10_000_000, sys_clk_hz=50_000_000, timeout=0.5)
        dev_slow.memory[0x20000] = 0xaaaa0001
        dev_slow.memory[0x20004] = 0xbbbb0002
        self.assertEqual(tx_slow.exchange(Op.READ, 0x20000), 0xaaaa0001)
        self.assertEqual(tx_slow.burst_read(1), [0xbbbb0002])
        self.assertFalse(dev_slow.pending)

    def test_errors_usb_disconnect_partial_read_and_no_retry(self):
        from nocpu_jtag import Op
        for mode, message, exception in (
                ('busy', 'occupied', RuntimeError), ('overrun', 'overrun', RuntimeError),
                ('address', 'address mismatch', RuntimeError),
                ('error', 'NPU error', RuntimeError), ('timeout', 'timed out', TimeoutError)):
            with self.subTest(mode=mode):
                dev, tx = self.make(timeout=0.005)
                if mode == 'busy':
                    dev.pending = True
                    dev.req_toggle ^= 1
                if mode == 'overrun':
                    dev.force_error = 1
                    tx.irscan(0x13)
                    tx.drscan(32, 0, False)
                    tx.irscan(0x12)
                if mode == 'address':
                    dev.address_xor = 4
                if mode == 'error':
                    dev.force_error = 2
                if mode == 'timeout':
                    dev.freeze = True
                if mode == 'overrun':
                    with self.assertRaisesRegex(exception, message):
                        tx.burst_check()
                else:
                    with self.assertRaisesRegex(exception, message):
                        tx.exchange(Op.READ, 0)

        # Ambiguous START timeout MUST NOT replay Op.START
        dev, tx = self.make(timeout=0.005)
        dev.freeze = True
        with self.assertRaises(TimeoutError):
            tx.exchange(Op.START)
        self.assertEqual(dev.packet >> 64, int(Op.START))
        self.assertEqual(len(dev.requests), 0)  # frozen before completion, submitted once only

        # Partial USB read triggers TimeoutError without retrying the command
        dev2, tx2 = self.make(timeout=0.005)
        dev2.partial_read_limit = 2
        with self.assertRaises(TimeoutError):
            tx2.exchange(Op.MODEL_COMMIT)
        self.assertLessEqual(sum(op == int(Op.MODEL_COMMIT) for op, _, _ in dev2.requests), 1)

        # Mid-transfer USB disconnect propagates OSError cleanly
        dev3, tx3 = self.make()
        dev3.fail_write_after = len(dev3.writes)
        with self.assertRaises(OSError):
            tx3.exchange(Op.READ, 0)

    def test_compiled_package_and_timing(self):
        from nocpu_jtag import Npu
        from npu_program import compile_model, NUMERIC_FORMAT
        from npu_e2e import TimedTransport, PhaseTimer
        pkg = compile_model(dict(input=dict(name='x', shape=[1, 1, 2]), nodes=[
            dict(op='FC', input='x', output='y', channels=1, weights=[1, 2], bias=[0])]), cols=8)
        dev, tx = self.make(frequency=10_000_000)
        dev.regs[0x6c] = NUMERIC_FORMAT
        dev.regs[0x58] = (pkg.manifest["rows"] << 16) | pkg.manifest["cols"]
        timer = PhaseTimer()
        npu = Npu(TimedTransport(tx, timer))
        timer.begin('load')
        npu.load_model(pkg)
        timer.finish()
        self.assertEqual(b''.join(dev.memory[0x10000 + i].to_bytes(4, 'little')
                                  for i in range(0, len(pkg.image), 4)), pkg.image)
        timer.begin('infer')
        self.assertEqual(npu.infer_values([1, 2]), [5])
        self.assertGreaterEqual(timer.finish()['total_wall_ms'], 0)
        self.assertEqual(dev.regs[4], 2)
        self.assertEqual(sum(op == 7 for op, _, _ in dev.requests), 1)


if __name__ == '__main__':
    unittest.main()
