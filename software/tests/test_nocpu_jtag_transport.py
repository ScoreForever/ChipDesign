"""Check actual OpenOCD field encoding/decoding for the 72-bit fabric protocol."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from nocpu_jtag import JtagTransport, TclRpc
from unittest.mock import Mock, patch


class Rpc:
    def __init__(self, reply):
        self.reply = reply
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return self.reply if command.startswith("drscan ") else ""


class JtagScanTests(unittest.TestCase):
    def test_packet_above_64_bits_uses_three_hex_fields(self):
        rpc = Rpc("89abcdef\n12345678\n80")
        transport = JtagTransport(rpc)
        result = transport.scan((0xab << 64) | (0x89abcdef << 32) | 0x01234567)
        self.assertEqual(rpc.commands[-1],
                         "drscan npu.tap 32 0x01234567 32 0x89abcdef 8 0xab -endstate IDLE")
        self.assertEqual(result, (0x80 << 64) | (0x12345678 << 32) | 0x89abcdef)

    def test_zero_poll_and_space_separated_reply(self):
        rpc = Rpc("00000000 00000000 00")
        self.assertEqual(JtagTransport(rpc).scan(0), 0)
        self.assertIn("32 0x00000000 32 0x00000000 8 0x00", rpc.commands[-1])

    def test_malformed_or_out_of_range_reply_is_rejected(self):
        for reply in ("error", "00000000 00000000", "00000000 00000000 100",
                      "00000000 100000000 00", "nothex 00000000 00", "00000000 00000000 -1"):
            with self.subTest(reply=reply):
                with self.assertRaisesRegex(RuntimeError, "OpenOCD drscan failed"):
                    JtagTransport(Rpc(reply)).scan(0)


if __name__ == "__main__":
    unittest.main()


class TclRpcFailures(unittest.TestCase):
    def make_rpc(self, chunks):
        sock = Mock()
        sock.recv.side_effect = chunks
        with patch('nocpu_jtag.socket.create_connection', return_value=sock):
            rpc = TclRpc(timeout=1)
        return rpc, sock

    def test_failed_frames_poison_connection(self):
        for chunks, error in (([TimeoutError()], TimeoutError),
                              ([b''], ConnectionError),
                              ([b'partial', b''], ConnectionError),
                              ([b'x' * 1_000_001], ConnectionError),
                              ([b'\xff\x1a'], UnicodeDecodeError),
                              ([OSError('disconnect')], OSError)):
            with self.subTest(error=error, chunks=len(chunks)):
                rpc, sock = self.make_rpc(chunks)
                with self.assertRaises(error):
                    rpc.execute('first')
                self.assertTrue(rpc._poisoned)
                sock.close.assert_called_once()
                count = sock.sendall.call_count
                with self.assertRaises(ConnectionError):
                    rpc.execute('second')
                self.assertEqual(sock.sendall.call_count, count)
                rpc.close()
                sock.close.assert_called_once()

    def test_fragmented_response_has_one_monotonic_deadline(self):
        rpc, sock = self.make_rpc([b'a', b'b', b'c\x1a'])
        with patch('nocpu_jtag.time.monotonic', side_effect=[0, 0, .1, .6, .7, 1.1]):
            with self.assertRaises(TimeoutError):
                rpc.execute('slow')
        self.assertTrue(rpc._poisoned)
        self.assertEqual(sock.recv.call_count, 2)

    def test_per_call_timeout_and_explicit_close(self):
        rpc, sock = self.make_rpc([b'ok\x1a'])
        self.assertEqual(rpc.execute('command', timeout=3), 'ok')
        rpc.close()
        with self.assertRaises(ConnectionError):
            rpc.execute('closed')
        self.assertFalse(rpc._poisoned)


class CompletedDeadlineFrame(unittest.TestCase):
    def test_completed_frame_wins_over_deadline(self):
        sock = Mock()
        sock.recv.return_value = b'complete\x1a'
        with patch('nocpu_jtag.socket.create_connection', return_value=sock):
            rpc = TclRpc(timeout=1)
        with patch('nocpu_jtag.time.monotonic', side_effect=[0, 0, .9, 2]):
            self.assertEqual(rpc.execute('command'), 'complete')
        self.assertFalse(rpc._poisoned)


import shutil
import subprocess
from nocpu_jtag import _EXCHANGE_TCL


@unittest.skipUnless(shutil.which('tclsh'), 'tclsh is missing')
class RealTclProtocolTests(unittest.TestCase):
    def run_tcl(self, script):
        result = subprocess.run(['tclsh'], input=_EXCHANGE_TCL + '\n' + script,
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stderr, '')
        return result.stdout.strip()

    def test_checked_reply_field_widths(self):
        for reply in ('100000000 0 80', '0 100000000 80', '0 0 180',
                      '-1 0 80', '0 zz 80'):
            with self.subTest(reply=reply):
                self.assertEqual(self.run_tcl("""
rename chipdesign_exchange_v5 original_exchange
proc chipdesign_exchange_v5 {args} { return {OK %s} }
puts [catch {chipdesign_checked_v5 tap 10 1 0 0} value]
puts $value
""" % reply), '1\nBAD_REPLY OK ' + reply)

    def test_pipeline_errors_are_rpc_results(self):
        for error in ('BUSY', 'OVERRUN', 'TIMEOUT', 'BURST_FAULT',
                      'ADDR_MISMATCH 4', 'NPU_ERR 5 7 0', 'BAD_REPLY truncated'):
            with self.subTest(error=error):
                self.assertEqual(self.run_tcl('''
rename chipdesign_checked_v5 original_checked
proc chipdesign_checked_v5 {args} { error {%s} }
foreach command {
    {chipdesign_reads_v5 tap 10 {0}}
    {chipdesign_load_v5 tap 10 1 {} {}}
    {chipdesign_infer_v5 tap 10 10 1 4 {0}}
} {
    set code [catch {eval $command} value]
    puts "$code:$value"
}
''' % error), '\n'.join(['0:' + error] * 3))

    def test_burst_read_pipeline_and_hex_formats(self):
        for last in ('89abcdef', '0x89abcdef'):
            self.assertEqual(self.run_tcl('''
proc irscan {args} {}
proc runtest {args} {}
proc drscan {args} { return deadbeef }
rename chipdesign_checked_v5 original_checked
proc chipdesign_checked_v5 {args} { return 0x80000000 }
rename chipdesign_scan_v5 original_scan
proc chipdesign_scan_v5 {args} { return {%s 00020004 80} }
puts [chipdesign_rblock_v5 tap 10 1 0x20000 2]
''' % last), '0x80000000 0x89abcdef')

    def test_bad_burst_replies(self):
        for reply in ('00 00', 'zz 00 80', '00 00 zz', '80000000 100000000 80',
                      '80000000 00 180'):
            self.assertEqual(self.run_tcl('''
proc irscan {args} {}
proc runtest {args} {}
proc drscan {args} { return 00 }
rename chipdesign_checked_v5 original_checked
proc chipdesign_checked_v5 {args} { return 0 }
rename chipdesign_scan_v5 original_scan
proc chipdesign_scan_v5 {args} { return {%s} }
puts [catch {chipdesign_rblock_v5 tap 10 1 0 2} value]
puts $value
''' % reply), '1\nBAD_REPLY ' + reply)

from types import SimpleNamespace
from nocpu_jtag import Npu, Op, ABI, NUMERIC_FORMAT
from npu_e2e import TimedTransport, PhaseTimer


class TapRpc:
    """Replay RPC history in Tcl so register and TAP state persist per call."""
    def __init__(self):
        self.commands = []
        self.history = []
        self.mode = ''
        self.events = []

    def execute(self, command, timeout=None):
        self.commands.append(command)
        script = self.model + '\n' + '\n'.join(self.history)
        script += '\nset mode {%s}\nset result [eval {%s}]\nputs "RESULT:$result"\nputs "EVENTS:$events"\n' % (self.mode, command)
        run = subprocess.run(['tclsh'], input=script, text=True, capture_output=True, check=True)
        if run.stderr:
            raise AssertionError(run.stderr)
        self.history.append('set mode {%s}\neval {%s}' % (self.mode, command))
        lines = run.stdout.splitlines()
        self.events = next(v[7:].split('|') for v in lines if v.startswith('EVENTS:'))
        return next(v[7:] for v in lines if v.startswith('RESULT:'))

    model = '''
set mode {}; set events {}; set ir 18; set addr 0; set reply {0 0 00}; set pending {}; set ready 1
array set mem {0 ABI 80 4096 84 4096 88 65537 92 0 96 128 108 NUMERIC 24 0 28 8 32 32 36 5 4 8 8 17 52 3 131104 2147483648 131108 305419896}
proc irscan {tap value args} {global ir events; set ir [expr {$value}]; append events "|ir:$ir"}
proc runtest {cycles} {global pending reply ready events; if {$cycles != 4} {error cycles}; set reply $pending; set ready 1; append events "|wait:$cycles"}
proc drscan {tap args} {
 global ir addr reply pending ready mem mode events
 append events "|dr:$ir:$args"
 if {$ir == 19} {incr addr 4; set mem($addr) [expr {[lindex $args 1]}]; append events "|write:$addr:$mem($addr)"; return 0}
 if {$ir == 20} {
   if {!$ready} {error {missing runtest 4}}
   set old [lindex $reply 0]; incr addr 4
   set value [format %x $mem($addr)]
   if {$mode eq "wide"} {set value 100000000}
   set status 80; if {$mode eq "burst"} {set status a0}
   set pending [list $value [format %x $addr] $status]; set ready 0; return $old
 }
 set op [expr {[lindex $args 5]}]
 if {!$op} {
   if {$mode eq "wide_addr" && ([string first "|ir:19" $events] >= 0 || [string first "|ir:20" $events] >= 0)} {return {80000000 100000000 80}}
   if {$mode eq "wide_status" && ([string first "|ir:19" $events] >= 0 || [string first "|ir:20" $events] >= 0)} {return {80000000 00020004 180}}
   if {$mode eq "busy"} {return {0 0 40}}; return $reply}
 set addr [expr {[lindex $args 3]}]; set data [expr {[lindex $args 1]}]
 append events "|op:$op:$addr"
 set status 80; set value 0
 if {$op == 1} {set value $mem($addr); if {$addr == 4 && $mode eq "fault"} {set value 72}; if {$addr == 4 && $mode eq "infer_timeout"} {set value 0}}
 if {$op == 2} {set mem($addr) $data; append events "|write:$addr:$data"}
 if {$op == 4 && $mode eq "commit"} {set status 85}
 if {$op == 7 && $mode eq "overrun"} {set status a0}
 if {$op == 7 && $mode eq "timeout"} {set status 00}
 set reply [list [format %x $value] [format %x $addr] $status]
 return {0 0 00}
}
'''.replace('ABI', str(ABI)).replace('NUMERIC', str(NUMERIC_FORMAT))


@unittest.skipUnless(shutil.which('tclsh'), 'tclsh missing')
class RealTclNpuTests(unittest.TestCase):
    def make(self, wordwise=False):
        rpc = TapRpc()
        timer = PhaseTimer()
        npu = Npu(TimedTransport(JtagTransport(rpc, timeout=.002), timer, wordwise))
        rpc.commands.clear()
        return rpc, npu, timer

    def package(self):
        return SimpleNamespace(image=b'\x00\x00\x00\x80' * 3, manifest=dict(
            abi=ABI, numeric_format=NUMERIC_FORMAT, packing='low_nibble_first',
            rows=1, cols=1, model_bytes=12, op_count=1, activation_peak=64,
            tensors={}, input_base=0, input_bytes=8, output_base=32,
            output_bytes=5, input_dtype='i32'))

    def test_load(self):
        rpc, npu, _ = self.make()
        npu.load_model(self.package())
        self.assertEqual([c.split()[0] for c in rpc.commands], ['chipdesign_reads_v5', 'chipdesign_load_v5'])
        self.assertIn('ir:19', rpc.events)
        for addr in (65536, 65540, 65544):
            self.assertIn(f'write:{addr}:2147483648', rpc.events)

    def test_infer_and_timing_and_wordwise(self):
        for wordwise in (False, True):
            rpc, npu, timer = self.make(wordwise)
            timer.begin('input_upload')
            self.assertEqual(npu.infer(bytes(8)), b'\0\0\0\x80\x78')
            report = timer.finish()
            self.assertAlmostEqual(sum(p['wall_ms'] for p in report['phases']), report['total_wall_ms'])
            self.assertEqual([p['phase'] for p in report['phases']], ['input_upload', 'completion_poll', 'ack'])
            transfer_ms = sum(p['wall_ms'] for p in report['phases'] if p['phase'] != 'completion_poll')
            compute_wait_ms = sum(p['wall_ms'] for p in report['phases'] if p['phase'] == 'completion_poll')
            self.assertGreaterEqual(transfer_ms, 0)
            self.assertGreaterEqual(compute_wait_ms, 0)
            self.assertEqual(len(rpc.commands), 2)
            self.assertTrue(rpc.commands[0].startswith('chipdesign_infer_v5'))
            self.assertTrue(rpc.commands[1].startswith('chipdesign_exchange_v5 npu.tap 0x08'))
            for ir in (19, 20):
                self.assertEqual(f'ir:{ir}' in rpc.events, not wordwise)

    def test_failure_matrix(self):
        for mode, message in [('busy', 'occupied'), ('overrun', 'may have executed'),
                              ('timeout', 'may have executed'), ('infer_timeout', 'may have executed'),
                              ('fault', 'NPU fault .* operator'), ('burst', 'BURST_FAULT'), ('wide', 'BAD_REPLY'),
                              ('wide_addr', 'BAD_REPLY'), ('wide_status', 'BAD_REPLY')]:
            with self.subTest(mode=mode):
                rpc, npu, _ = self.make()
                rpc.mode = mode
                with self.assertRaisesRegex((RuntimeError, TimeoutError), message):
                    npu.infer(bytes(8), timeout=.002)
                self.assertEqual(len(rpc.commands), 1)
                self.assertNotIn('op:8:0', rpc.events)
                for op in (5, 6, 7):
                    self.assertLessEqual(rpc.events.count(f'op:{op}:0'), 0 if mode == 'busy' else 1)
                if mode == 'fault':
                    for addr in (8, 52): self.assertIn(f'op:1:{addr}', rpc.events)

    def test_bad_write_burst_aborts_before_commit(self):
        for mode in ('wide_addr', 'wide_status'):
            with self.subTest(mode=mode):
                rpc, npu, _ = self.make()
                rpc.mode = mode
                with self.assertRaisesRegex(RuntimeError, 'BAD_REPLY'):
                    npu.load_model(self.package())
                self.assertIn('ir:19', rpc.events)
                self.assertNotIn('op:4:0', rpc.events)

    def test_input_length(self):
        rpc, npu, _ = self.make()
        with self.assertRaises(ValueError): npu.infer(b'x')
        self.assertEqual(len(rpc.commands), 1)
        self.assertNotIn('op:5:0', rpc.events)

    def test_manifest_failure_boundaries(self):
        rpc, npu, _ = self.make()
        old = {'previous': True}
        npu.manifest = old
        package = self.package()
        package.manifest['cols'] = 2
        with self.assertRaises(ValueError): npu.load_model(package)
        self.assertIs(npu.manifest, old)
        rpc.mode = 'commit'
        with self.assertRaises(RuntimeError): npu.load_model(self.package())
        self.assertFalse(hasattr(npu, 'manifest'))
