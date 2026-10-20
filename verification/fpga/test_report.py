"""Negative evidence tests; use temporary files, never modify board evidence."""
import json
from pathlib import Path
import tempfile
import unittest
import report

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.out=Path(self.temp.name)
        (self.out/'audit').mkdir()
    def tearDown(self): self.temp.cleanup()
    def test_timing_must_match_raw_report(self):
        p=self.out/'audit/timing.json'
        p.write_text(json.dumps(dict(wns_ns=999,whs_ns=999)))
        (self.out/'audit/route_timing_summary.rpt').write_text('WNS(ns) TNS(ns) Failing Total WHS(ns)\n-------\n 1.053 0.000 0 41103 0.009\n')
        with self.assertRaisesRegex(ValueError,'mismatch'):
            report.verify_timing(self.out,dict(timing_json_sha256=report.sha(p)))
    def test_comment_is_not_constraint_evidence(self):
        p=self.out/'audit/dmi_constraints.tsv';p.write_text('# set_max_delay\n')
        with self.assertRaisesRegex(ValueError,'coverage'):
            report.verify_dmi_constraints(self.out,dict(dmi_constraints_sha256=report.sha(p)))
    def test_cut_path_is_rejected(self):
        p=self.out/'audit/dmi_constraints.tsv'
        p.write_text('group\tendpoints\tpaths\tbound_ns\tmin_slack_ns\n'+''.join(
            f'{c}/{k}\t1\t0\t20\t19\n' for c in ('req','resp') for k in ('data','req','ack')))
        with self.assertRaisesRegex(ValueError,'cut'):
            report.verify_dmi_constraints(self.out,dict(dmi_constraints_sha256=report.sha(p)))
    def test_bad_reset_state_is_rejected(self):
        item=dict(status='PASS',jobs=2,logits=[1],reset_state={'0x70000008':0})
        with self.assertRaisesRegex(ValueError,'control state'):
            report.verify_reset(self.out,item,[1])
    def test_real_routed_constraints(self):
        p=report.ROOT/'build/fpga_cdc_closure_20261020_r2/audit/dmi_constraints.tsv'
        if not p.exists(): self.skipTest('Local routed evidence unavailable')
        report.verify_dmi_constraints(p.parents[1],dict(dmi_constraints_sha256=report.sha(p)))

if __name__=='__main__': unittest.main()
