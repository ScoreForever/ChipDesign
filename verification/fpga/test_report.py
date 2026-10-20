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
    def io_rows(self):
        return [dict(port=p,type=t,paths=1,io_delay_ns=((41 if t=='max' else -20) if p=='td_o' else (32.5 if t=='max' else -25)),
            launch_edge_ns=500,capture_edge_ns=1000 if t=='max' else 0,min_slack_ns=400,direct_paths=1,equivalent_paths=0)
            for p in ('td_i','tms_i','td_o') for t in ('max','min')]
    def check_io(self,rows):
        import csv
        path=self.out/'audit/io_audit';path.mkdir(exist_ok=True)
        path=path/'jtag_io.tsv'
        with path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=self.io_rows()[0].keys(),delimiter='\t');writer.writeheader();writer.writerows(rows)
        return report.verify_jtag_io(self.out,dict(jtag_io_sha256=report.sha(path)))
    def test_io_correct_envelope(self):
        self.assertEqual(len(self.check_io(self.io_rows())),6)
    def test_io_equivalent_reference_edges(self):
        rows=self.io_rows()
        for row in rows:
            row['io_delay_ns']+=500;row['direct_paths']=0;row['equivalent_paths']=1
            if row['port']=='td_o': row['capture_edge_ns']+=500
            else: row['launch_edge_ns']=0
        self.assertEqual(len(self.check_io(rows)),6)
    def test_io_wrong_edge_rejected(self):
        rows=self.io_rows();rows[0]['launch_edge_ns']=0
        with self.assertRaisesRegex(ValueError,'edge mismatch'): self.check_io(rows)
    def test_io_missing_hold_rejected(self):
        with self.assertRaisesRegex(ValueError,'coverage'): self.check_io(self.io_rows()[:-1])
    def test_io_cut_path_rejected(self):
        rows=self.io_rows();rows[0]['paths']=0;rows[0]['direct_paths']=0
        with self.assertRaisesRegex(ValueError,'cut'): self.check_io(rows)
    def test_io_negative_slack_rejected(self):
        rows=self.io_rows();rows[0]['min_slack_ns']=-0.1
        with self.assertRaisesRegex(ValueError,'failed'): self.check_io(rows)

if __name__=='__main__': unittest.main()
