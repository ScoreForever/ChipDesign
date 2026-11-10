"""Run the mailbox Tcl against a scripted TAP, including ambiguous outcomes."""
from pathlib import Path
import shutil,subprocess,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from nocpu_jtag import JtagTransport,Op,_EXCHANGE_TCL

class TclTap:
    def __init__(self,replies):self.replies=replies;self.calls=0;self.scans=0;self.submits=0
    def execute(self,command):
        if not command.startswith('chipdesign_exchange_v5 '):return ''
        self.calls+=1
        queue=' '.join('{'+x+'}' for x in self.replies)
        script=_EXCHANGE_TCL+'\nset replies {'+queue+'''}
set scans 0
set submits 0
proc drscan {args} {
    global replies scans submits
    incr scans
    if {[expr [lindex $args 6]] != 0} { incr submits }
    if {[llength $replies] == 0} { return {00000000 00000000 00} }
    set v [lindex $replies 0]
    set replies [lrange $replies 1 end]
    return $v
}
'''+f'puts "ANSWER [{command}]"\nputs "SCANS $scans"\nputs "SUBMITS $submits"\n'
        result=subprocess.run(['tclsh'],input=script,text=True,capture_output=True,timeout=3)
        fields=dict(line.split(' ',1) for line in result.stdout.splitlines() if ' ' in line)
        if 'ANSWER' not in fields:raise AssertionError('Tcl script failed: '+result.stderr)
        self.scans=int(fields['SCANS']);self.submits=int(fields['SUBMITS'])
        return fields['ANSWER']

@unittest.skipUnless(shutil.which('tclsh'),'tclsh required for actual Tcl execution')
class BatchedMailboxTests(unittest.TestCase):
    def test_pending_reply_then_completion_uses_one_rpc_and_one_submission(self):
        rpc=TclTap(['00000000 00000000 00','00000000 00000000 00','00000000 00000000 40','89abcdef 12345678 80'])
        value=JtagTransport(rpc).exchange(Op.READ,0x12345678)
        self.assertEqual(value,0x89abcdef);self.assertEqual((rpc.calls,rpc.scans,rpc.submits),(1,4,1))
    def test_busy_does_not_submit(self):
        rpc=TclTap(['00000000 00000000 40'])
        with self.assertRaisesRegex(RuntimeError,'occupied'):JtagTransport(rpc).exchange(Op.START)
        self.assertEqual((rpc.calls,rpc.submits),(1,0))
    def test_address_mismatch_is_rejected(self):
        rpc=TclTap(['0 0 00','0 0 00','0 00000011 80'])
        with self.assertRaisesRegex(RuntimeError,'address mismatch'):JtagTransport(rpc).exchange(Op.READ,0x10)
        self.assertEqual(rpc.submits,1)
    def test_device_error_is_preserved(self):
        rpc=TclTap(['0 0 00','0 0 00','0 00000010 84'])
        with self.assertRaisesRegex(RuntimeError,'NPU error 4'):JtagTransport(rpc).exchange(Op.READ,0x10)
    def test_overrun_is_ambiguous_and_not_retried(self):
        rpc=TclTap(['0 0 00','0 0 00','0 0 a0'])
        with self.assertRaisesRegex(RuntimeError,'may have executed'):JtagTransport(rpc).exchange(Op.START)
        self.assertEqual((rpc.calls,rpc.submits),(1,1))
    def test_timeout_does_not_replay_start(self):
        rpc=TclTap(['0 0 00','0 0 00'])
        with self.assertRaisesRegex(TimeoutError,'may have executed'):JtagTransport(rpc,timeout=.005).exchange(Op.START)
        self.assertEqual((rpc.calls,rpc.submits),(1,1))
    def test_rpc_timeout_is_ambiguous_and_not_retried(self):
        class Dropped:
            calls=0
            def execute(self,command):
                if command.startswith('chipdesign_exchange_v5 '):
                    self.calls+=1;raise TimeoutError('connection timed out')
                return ''
        rpc=Dropped()
        with self.assertRaisesRegex(TimeoutError,'may have executed'):JtagTransport(rpc).exchange(Op.START)
        self.assertEqual(rpc.calls,1)

    def test_connection_loss_is_ambiguous_and_not_retried(self):
        class Disconnected:
            calls=0
            def execute(self,command):
                if command.startswith('chipdesign_exchange_v5 '):
                    self.calls+=1;raise ConnectionError('peer closed')
                return ''
        rpc=Disconnected()
        with self.assertRaisesRegex(ConnectionError,'may have executed'):JtagTransport(rpc).exchange(Op.START)
        self.assertEqual(rpc.calls,1)
    def test_malformed_completed_reply_is_rejected_without_replay(self):
        rpc=TclTap(['0 0 00','0 0 00','100000000 00000010 80'])
        with self.assertRaisesRegex(RuntimeError,'drscan failed'):JtagTransport(rpc).exchange(Op.READ,0x10)
        self.assertEqual((rpc.calls,rpc.submits),(1,1))
    def test_invalid_request_never_submits(self):
        rpc=TclTap([]);tx=JtagTransport(rpc)
        with self.assertRaises(ValueError):tx.exchange(0)
        with self.assertRaises(ValueError):tx.exchange(Op.WRITE,0,2**32)
        self.assertEqual(rpc.calls,0)

if __name__=='__main__':unittest.main()
