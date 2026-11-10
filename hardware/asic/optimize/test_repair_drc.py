from pathlib import Path
import subprocess
import tempfile
import unittest


class RepairDrcTests(unittest.TestCase):
    def run_fixture(self, reports):
        with tempfile.TemporaryDirectory() as directory:
            script = 'set repairs 0\nset reports [list ' + ' '.join('{' + r + '}' for r in reports) + ']\n'
            script += '''proc verify_drc {args} {
 global reports
 set fp [open [lindex $args end] w]
 puts $fp [lindex $reports 0];close $fp
 set reports [lrange $reports 1 end]
}
proc ecoRoute {args} {
 global repairs
 if {$args ne "-fix_drc"} {error "Unexpected repair command"}
 incr repairs
}
'''
            script += 'source {' + str(Path(__file__).with_name('repair_drc.tcl')) + '}\n'
            script += 'set failed [catch {repair_signal_drc} reason]\nputs "$failed $repairs $reason"\n'
            result = subprocess.run(['tclsh'], input=script, text=True, cwd=directory, capture_output=True, check=True)
            self.assertEqual(result.stderr, '')
            return result.stdout.strip(), sorted(p.name for p in Path(directory).glob('signal_drc_attempt_*.rpt'))

    def test_rechecks_short_repair_and_preserves_both_reports(self):
        result, reports = self.run_fixture(['Total Violations : 2 Viols.', 'No DRC violations were found'])
        self.assertEqual(result, '0 1')
        self.assertEqual(len(reports), 2)

    def test_rejects_persistent_violations_after_two_repairs(self):
        result, reports = self.run_fixture(['Total Violations : 2 Viols.'] * 3)
        self.assertTrue(result.startswith('1 2 Signal DRC remains'))
        self.assertEqual(len(reports), 3)

    def test_rejects_unknown_report_without_routing(self):
        result, _ = self.run_fixture(['incomplete tool report'])
        self.assertTrue(result.startswith('1 0 Unrecognized signal DRC report'))


if __name__ == '__main__':
    unittest.main()
