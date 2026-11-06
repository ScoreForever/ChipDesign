"""Exercise the real Tcl acceptance gate with routed good/bad CDC reports."""
from pathlib import Path
import tempfile
import tkinter
import unittest

ROOT=Path(__file__).resolve().parents[3]


class CriticalCdcGate(unittest.TestCase):
    def setUp(self):
        self.tcl=tkinter.Tcl()
        self.tcl.call('source',str(ROOT/'hardware/fpga/scripts/constrain_npu_jtag_cdc.tcl'))

    def test_existing_reviewed_mailbox_report_is_accepted(self):
        self.tcl.call('reject_critical_npu_jtag_cdc',str(ROOT/
            'verification/fpga/results/2026-11-01-no-cpu-int4/evidence/validated_50mhz/route_cdc.rpt'))

    def test_actual_async_burst_error_path_blocks_acceptance(self):
        with self.assertRaisesRegex(tkinter.TclError,'Critical CDC'):
            self.tcl.call('reject_critical_npu_jtag_cdc',str(ROOT/
                'verification/fpga/fixtures/cdc_critical_burst_v2.rpt'))

    def test_missing_or_unrecognized_report_cannot_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'report.rpt'
            with self.assertRaises(tkinter.TclError):
                self.tcl.call('reject_critical_npu_jtag_cdc',str(path))
            path.write_text('CDC Report\nNo recognized summaries\n')
            with self.assertRaisesRegex(tkinter.TclError,'Unrecognized'):
                self.tcl.call('reject_critical_npu_jtag_cdc',str(path))


if __name__=='__main__':unittest.main()
