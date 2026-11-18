from pathlib import Path
import subprocess
import tempfile
import unittest


class WirebondKeepoutTests(unittest.TestCase):
    def test_uses_actual_pad_boxes_and_clips_only_upper_metal_keepouts(self):
        script = '''proc dbGet {args} {
 if {[llength $args]==3} {return {B0 B1}}
 switch -- [lindex $args 0] {
  top.fPlan.box {return {{0 0 1940.4 1940.4}}}
  B0.box {return {{170.5 1867.24 230.5 1940.4}}}
  B1.box {return {{0 220 73.16 280}}}
  B0.name {return bond_N}
  B1.name {return bond_W}
  default {error "unexpected query $args"}
 }
}
set calls {}
proc createRouteBlk {args} {global calls;lappend calls $args}
'''
        script += 'source {' + str(Path(__file__).with_name('wirebond_keepout.tcl')) + '}\n'
        script += '''if {![catch {add_wirebond_keepouts 35}]} {error "missing PADs accepted"}
if {[llength $calls]!=0} {error "modified design before validating PAD count"}
if {[add_wirebond_keepouts 2]!=2} {error "wrong count"}
foreach call $calls {puts $call}
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['tclsh'], input=script, text=True, cwd=directory, capture_output=True, check=True)
            self.assertEqual(result.stderr, '')
            rows = result.stdout.splitlines()
            self.assertEqual(len(rows), 4)
            self.assertIn('-box {167.5 1864.24 233.5 1940.4} -exceptpgnet', rows[0])
            self.assertIn('-box {0 217.0 76.16 283.0}', rows[2])
            self.assertTrue(all('-layer {METAL5 METAL6}' in row for row in rows))
            self.assertTrue(rows[1].endswith('-fills'))
            self.assertEqual(len((Path(directory)/'wirebond_keepouts.tsv').read_text().splitlines()), 2)


if __name__ == '__main__':
    unittest.main()
