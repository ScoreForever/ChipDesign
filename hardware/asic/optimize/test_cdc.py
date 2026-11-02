import csv
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import cdc_check
import flow


class CdcTests(unittest.TestCase):
    def rows(self):
        return [dict(kind=k, **{'from': a, 'to': b}, mode=m,
                     arrival_ns='1', required_ns='20', slack_ns='19')
                for k, a, b, m in sorted(cdc_check.expected_paths())]

    def check_rows(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'cdc.tsv'
            with path.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=['kind','from','to','mode','arrival_ns','required_ns','slack_ns'], delimiter='\t')
                writer.writeheader(); writer.writerows(rows)
            return cdc_check.validate(path)

    def test_exact_bit_mapping_and_timing_required(self):
        self.assertTrue(self.check_rows(self.rows()))
        self.assertFalse(self.check_rows(self.rows()[:-1]))
        rows=self.rows(); rows[-1]=rows[0].copy()
        self.assertFalse(self.check_rows(rows))
        rows=self.rows(); rows[0]['to']='i_host_cmd_data_o_reg[99]/D'
        self.assertFalse(self.check_rows(rows))
        for change in ({'slack_ns':'nan'}, {'slack_ns':'-0.001'},
                       {'required_ns':'1000','slack_ns':'999'},
                       {'arrival_ns':'1','required_ns':'20','slack_ns':'1'}):
            rows=self.rows(); row=next(r for r in rows if r['kind']=='request'); row.update(change)
            self.assertFalse(self.check_rows(rows))

    def test_legacy_clock_group_handoff_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'old.sdc'
            p.write_text('set_units -time ns\ncreate_clock -name core_clk -period 20\ncreate_clock -name jtag_clk -period 1000\nset_clock_groups -asynchronous -group core_clk -group jtag_clk\n')
            with self.assertRaisesRegex(RuntimeError,'clock-group'): flow.verify_clocks(p)

    def test_stale_success_cannot_hide_missing_cdc_evidence(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            missing=flow.metrics(root)['missing']
            for name in missing:
                p=root/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('evidence')
            (root/'status.json').write_text(json.dumps(dict(status='complete')))
            (root/'metrics.json').write_text(json.dumps(dict(setup_wns_ns=.1,hold_wns_ns=.1,setup_tns_ns=0,hold_tns_ns=0,die_area_mm2=1,wire_length_um=100,macro_count=2)))
            checks={k:True for k in ['placement','drc','pg_drc','connectivity','pg_connectivity','antenna','drv','ports','pad_geometry','cdc']}
            (root/'checks.json').write_text(json.dumps(checks))
            self.assertFalse(flow.metrics(root)['eligible'])
            with (root/'cdc_paths.tsv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(self.rows()[0]),delimiter='\t')
                writer.writeheader();writer.writerows(self.rows())
            self.assertTrue(flow.metrics(root)['eligible'])
            (root/'cdc_paths.tsv').unlink()
            self.assertFalse(flow.metrics(root)['eligible'])

    @unittest.skipUnless(shutil.which('tclsh'), 'Tcl interpreter unavailable')
    def test_tcl_constraints_and_negative_audits(self):
        # API model exercises Tcl control/selection and fail-closed behavior;
        # this deliberately does not claim Cadence syntax/tool qualification.
        model=r'''
proc foreach_in_collection {v xs body} {
    foreach x $xs {uplevel 1 [list set $v $x]; uplevel 1 $body}
}
proc sizeof_collection {xs} {return [llength $xs]}
set cells {}
foreach {stem width} {req_payload 72 cmd_data_o 32 cmd_addr_o 32 cmd_op_o 8 rsp_mailbox_data 32 rsp_mailbox_error 5 reply_data 32 reply_error 5 req_sync 2 ack_sync 2 tck_reset_sync 2 sys_reset_sync 2} {
    for {set i 0} {$i<$width} {incr i} {lappend cells [format {i_host_%s_reg[%d]} $stem $i]}
}
lappend cells i_host_req_toggle_reg i_host_ack_toggle_reg
proc get_cells {args} {
    if {[lsearch -exact $args -filter]<0} {return $::cells}
    regexp {full_name == "([^"]+)"} [lindex $args end] -> name
    return [list $name]
}
proc get_pins {args} {
    set cell [lindex [lindex $args 1] 0]
    regexp {name == "([^"]+)"} [lindex $args end] -> terminal
    return [list $cell/$terminal]
}
proc get_object_name {obj} {return $obj}
set delays 0; set holds 0; set entries 0
proc set_max_delay {args} {
    if {[lsearch -exact $args -ignore_clock_latency]<0} {error "missing clock latency exclusion"}
    incr ::delays
}
proc set_false_path {args} {
    if {[lsearch -exact $args -hold]>=0} {incr ::holds}
    if {[lsearch -exact $args -from]>=0 && [lsearch -exact $args -hold]<0} {incr ::entries}
}
proc report_timing {args} {
    if {$::fault eq "cut"} {return {}}
    return [list path]
}
proc get_property {obj name} {
    switch $name {
        slack {if {$::fault eq "negative"} {return -1}; return 19}
        arrival_time {return 1}
        required_time {if {$::fault eq "overridden"} {return 1000}; return 20}
    }
    error "unmodeled property $name"
}
'''
        source=Path(__file__).with_name('npu_jtag_cdc.tcl').as_posix()
        for fault in ('none','cut','negative','overridden','missing'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                script=Path(tmp)/'run.tcl'; report=(Path(tmp)/'audit.tsv').as_posix()
                code=model+f'\nset fault {fault}\nsource {{{source}}}\n'
                if fault=='missing': code+='set cells [lrange $cells 1 end]\n'
                code+='if {[catch {npu_cdc::apply; npu_cdc::audit {'+report+'}} message]} {puts stderr $message; exit 1}\n'
                code+='if {$delays!=109 || $holds!=109 || $entries!=2} {exit 2}\n'
                script.write_text(code)
                result=subprocess.run(['tclsh',str(script)],capture_output=True,text=True,timeout=10)
                if fault=='none':
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertTrue(cdc_check.validate(report),Path(report).read_text()[:1000])
                else: self.assertNotEqual(result.returncode,0)


if __name__=='__main__': unittest.main()
