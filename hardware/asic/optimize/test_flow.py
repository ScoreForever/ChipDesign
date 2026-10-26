import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import flow

SIZES={'RA1SHD_2048x32M8':[554.595,723.85],'PCORNER':[114.84,114.84],
       'PDDW0204CDG':[80,114.84],**{cell:[80,114.84] for _,cell in flow.SUPPLIES}}

class FlowTests(unittest.TestCase):
    def test_geometry_places_two_macros_with_clearance_and_all_seven_signals(self):
        std=1100000  # roughly the expected dual-SRAM standard-cell area
        a=flow.geometry(std,SIZES,.55)
        b=flow.geometry(std,SIZES,.7)
        self.assertLess(b['die_side_um'],a['die_side_um'])
        self.assertEqual(len([p for p in a['pads'] if p['port']]),7)
        self.assertEqual({p['side'] for p in a['pads']},{'N','S','E','W'})
        for layout in flow.LAYOUTS:
            g=flow.geometry(std,SIZES,.6,layout);margin=g['margin_um'];core=g['core_side_um']
            self.assertEqual([m[0] for m in g['macros']],['model','activation'])
            boxes=[m[1:] for m in g['macros']]
            for x,y,w,h in boxes:
                self.assertGreater(x,margin+26);self.assertLess(x+w,margin+core-26)
                self.assertGreater(y,margin+26);self.assertLess(y+h,margin+core-26)
                for v in (x,y):self.assertAlmostEqual(v/.005,round(v/.005))
            (x0,y0,w0,h0),(x1,y1,w1,h1)=boxes
            # Both halos (26um each) plus a routing channel separate the macros.
            gap=max(x1-(x0+w0),x0-(x1+w1),y1-(y0+h0),y0-(y1+h1))
            self.assertGreaterEqual(gap,2*26)
        with self.assertRaises(ValueError):flow.geometry(std,SIZES,.6,'nowhere')
    def test_area_budget_skips_oversized_candidates_without_tools(self):
        # The die cap is 2x2 mm; an oversized point never launches Innovus.
        self.assertEqual(flow.MAX_DIE_UM,2000)
        self.assertGreater(flow.geometry(4e6,SIZES,.55)['die_side_um'],flow.MAX_DIE_UM)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);syn=root/'synthesis';syn.mkdir()
            for f in ('mapped.v','mapped.sdc'):(syn/f).write_text(f)
            flow.save(syn/'area.json',dict(standard_cell_area_um2=4e6))
            flow.save(syn/'handoff.json',{p:flow.digest(syn/p) for p in ('mapped.v','mapped.sdc','area.json')})
            libs=dict(macro_lef=root/'a',io_lef=root/'b',bond_lef=root/'c')
            with patch('flow.lef_sizes',return_value=SIZES),patch('flow.launch') as launch:
                result=flow.candidate(root,libs,'big',.55,'west_stack')
            launch.assert_not_called()
            self.assertFalse(result['eligible']);self.assertFalse(result['within_area_budget'])
            self.assertFalse((root/'candidates/big/status.json').exists())
    def test_invalid_geometry_and_tcl_paths_fail(self):
        for util in (0,1,-1):
            with self.assertRaises(ValueError):flow.geometry(100,SIZES,util)
        with self.assertRaises(ValueError):flow.tcl('/tmp/{invalid}')
    def test_lef_parser(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.lef';p.write_text('MACRO A\n SIZE 4.5 BY 6 ;\n  END A\nMACRO B\n SIZE 1 BY 2 ;\nEND B\n')
            self.assertEqual(flow.lef_sizes([p]),{'A':[4.5,6.0],'B':[1.,2.]})
    def test_def_wire_lengths_do_not_join_nets_or_vias(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.def';p.write_text('UNITS DISTANCE MICRONS 1000 ;\nNETS 2 ;\n- a + ROUTED METAL2 ( 100 200 ) ( 1100 * ) VIA\n NEW METAL3 ( 1100 200 ) ( * 2200 ) ;\n- b + ROUTED METAL2 ( 0 0 ) ( 3000 0 ) ;\nEND NETS\n')
            self.assertEqual(flow.def_wire_length(p),6)
    def test_zero_exit_without_marker_is_failure(self):
        with tempfile.TemporaryDirectory() as d:
            state=flow.launch(['/bin/sh','-c','echo "Error : failed"'],Path(d),'COMPLETE')
            self.assertEqual(state['status'],'failed')
            self.assertTrue(state['errors'])
    def test_timeout_is_preserved_and_previous_run_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            state=flow.launch(['/bin/sh','-c','sleep 5'],path,'COMPLETE',timeout=.02)
            self.assertEqual(state['status'],'failed')
            self.assertIn('timeout',state['reason'])
            with self.assertRaises(RuntimeError):flow.launch(['/bin/true'],path,'COMPLETE')
    def test_missing_or_failed_reports_cannot_rank(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(flow.metrics(Path(d))['eligible'])
        rows=[dict(eligible=True,die_area_mm2=8,setup_wns_ns=.4,wire_length_um=5),
              dict(eligible=False,die_area_mm2=1),dict(eligible=True,die_area_mm2=7,setup_wns_ns=.1,wire_length_um=9)]
        self.assertEqual(flow.rank(rows)[0]['die_area_mm2'],7)
    def test_report_validator_requires_positive_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            self.assertFalse(any(flow.validate_reports(p).values()))
            reports={'drc.rpt':'No DRC violations were found','pg_drc.rpt':'No DRC violations were found',
             'antenna.rpt':'No Violations Found','connectivity.rpt':'Found no problems or warnings.',
             'pg_connectivity.rpt':'Found no problems or warnings.',
             'placement.rpt':'Number of Unplaced Instances = 0','constraints.rpt':'No constrained paths found',
             'ports.txt':'clk_i rst_ni tck_i tms_i td_i td_o irq_o',
             'io_geometry.tsv':'pad PDDW0204CDG R0 0 0 80 114.84 1000\n'}
            for name,text in reports.items():(p/name).write_text(text)
            checks=flow.validate_reports(p)
            self.assertTrue(checks['placement']);self.assertTrue(checks['pad_geometry']);self.assertFalse(checks['drv'])
            (p/'io_geometry.tsv').write_text(reports['io_geometry.tsv']+'bad PDDW0204CDG R0 20 0 100 114.84 1000\n')
            self.assertFalse(flow.validate_reports(p)['pad_geometry'])
            (p/'placement.rpt').write_text('set pchkOverlapList [list bad]\nNumber of Unplaced Instances = 0')
            self.assertFalse(flow.validate_reports(p)['placement'])

    def test_external_synthesis_requires_clean_marker_and_keeps_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=base/'original';output=base/'experiment';source.mkdir();output.mkdir()
            flow.save(source/'rtl_sources.json',dict(sources_sha256=flow.rtl_fingerprint()))
            macro=base/'macro.lib';macro.write_text('cell (RA1SHD_2048x32M8) { area : 400; }')
            (source/'console.log').write_text('EDA_MAPPED_SYNTHESIS_COMPLETE\nError : failed\n')
            with self.assertRaises(RuntimeError):flow.adopt_synthesis(output,{'macro_lib':macro},source)
            (source/'console.log').write_text('EDA_MAPPED_SYNTHESIS_COMPLETE\n')
            for name in ('run.sh','run.tcl','my_soc_top.v','my_soc_top.sdc','timing.rpt'):(source/name).write_text('retained source')
            (source/'my_soc_top.v').write_text('module my_soc_top();\n  RA1SHD_2048x32M8 i_model(.CLK(c));\nendmodule\n')
            (source/'my_soc_top.sdc').write_text('set_units -time ns\ncreate_clock -name core_clk -period 20 [get_ports clk_i]\ncreate_clock -name jtag_clk -period 1000 [get_ports tck_i]\n')
            (source/'exit_code').write_text('0');(source/'area.rpt').write_text('my_soc_top 123 1500.0 200.0\n')
            with self.assertRaisesRegex(RuntimeError,'expected 2'):flow.adopt_synthesis(output,{'macro_lib':macro},source)
            (source/'my_soc_top.v').write_text('module my_soc_top();\n  RA1SHD_2048x32M8 i_model(.CLK(c));\n  RA1SHD_2048x32M8 i_act(.CLK(c));\nendmodule\n')
            (source/'my_soc_top.sdc').write_text('set_units -time ns\ncreate_clock -name core_clk -period 20 [get_ports clk_i]\ncreate_clock -name jtag_clk -period 1000 [get_ports tck_i]\n')
            (source/'exit_code').write_text('0')
            (source/'area.rpt').write_text('my_soc_top 123 1500.0 200.0\n')
            flow.adopt_synthesis(output,{'macro_lib':macro},source)
            area=json.loads((output/'synthesis/area.json').read_text())
            self.assertEqual(area['standard_cell_area_um2'],700);self.assertEqual(area['macro_count'],2)
            self.assertEqual((output/'synthesis/mapped.v').read_bytes(),(source/'my_soc_top.v').read_bytes())
            with self.assertRaises(RuntimeError):flow.adopt_synthesis(output,{'macro_lib':macro},source)

    def test_clock_units_are_never_silently_reinterpreted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.sdc'
            p.write_text('set_units -time ps\ncreate_clock -name core_clk -period 20000\ncreate_clock -name jtag_clk -period 1000000\n')
            with self.assertRaises(RuntimeError):flow.verify_clocks(p)
            p.write_text('set_units -time ns\ncreate_clock -name core_clk -period 20\ncreate_clock -name jtag_clk -period 1000\n')
            self.assertEqual(flow.verify_clocks(p)['core_clk'],20)
            # Genus writes the 1 ns unit as 1000ps; that is the same scale.
            p.write_text('set_units -capacitance 1000fF\nset_units -time 1000ps\ncreate_clock -name "core_clk" -period 20.0 [get_ports clk_i]\ncreate_clock -name "jtag_clk" -period 1000.0 [get_ports tck_i]\n')
            self.assertEqual(flow.verify_clocks(p)['jtag_clk'],1000)
            for bad in ('100ps','10ns','1us'):
                p.write_text(f'set_units -time {bad}\ncreate_clock -name core_clk -period 20\ncreate_clock -name jtag_clk -period 1000\n')
                with self.assertRaises(RuntimeError):flow.verify_clocks(p)

    def test_driver_snapshot_is_independent_of_later_source_edits(self):
        with tempfile.TemporaryDirectory() as d:
            copied=flow.snapshot_driver(Path(d))
            hashes=json.loads((copied/'sha256.json').read_text())
            self.assertEqual(hashes['fullchip.tcl'],flow.digest(flow.HERE/'fullchip.tcl'))
            self.assertTrue((copied/'legacy/init_pnr_standard.tcl').is_file())
            self.assertFalse(any('lec' in p or p.endswith('.lib') for p in hashes))

    def test_antenna_eco_selects_only_reported_literal_bus_net(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            script='''set attempts 0
set selected {}
proc verifyProcessAntenna {args} {
    global attempts
    set f [open [lindex $args 1] w]
    if {$attempts==0} {puts $f {sram_wdata[0] (4)}} else {puts $f {No Violations Found}}
    close $f;incr attempts
}
proc setNanoRouteMode {args} {}
proc editDelete {args} {global selected;lappend selected [lindex $args 1]}
proc deselectAll {} {}
proc selectNet {name} {}
proc routeDesign {args} {}
'''
            script+='source {'+str(flow.HERE/'repair_antenna.tcl')+'}\nrepair_process_antenna\nputs $selected\n'
            run=subprocess.run(['tclsh'],cwd=d,input=script,text=True,capture_output=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertFalse(run.stderr)
            self.assertEqual(run.stdout.strip(),'{sram_wdata[0]}')

    def test_controller_disconnect_keeps_real_tool_exit_receipt(self):
        import os,subprocess,sys,time
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)
            code="import sys;from pathlib import Path;import flow;flow.launch(['/bin/sh','-c','sleep .2; echo COMPLETE'],Path(sys.argv[1]),'COMPLETE')"
            env={**os.environ,'PYTHONPATH':str(flow.HERE)}
            parent=subprocess.Popen([sys.executable,'-c',code,d],env=env)
            try:
                deadline=time.time()+3
                while time.time()<deadline:
                    if (directory/'status.json').exists() and json.loads((directory/'status.json').read_text()).get('pid'):break
                    time.sleep(.01)
                parent.terminate();parent.wait(timeout=3)
                while time.time()<deadline and not (directory/'tool_exit.json').exists():time.sleep(.01)
                recovered=flow.recover_tool(directory,'COMPLETE')
                self.assertEqual(recovered['status'],'complete')
                self.assertEqual(recovered['exit_code'],0)
            finally:
                if parent.poll() is None:parent.kill();parent.wait()

    def test_missing_exit_receipt_is_never_assumed_success(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            flow.save(p/'status.json',dict(status='running',started=0,pid=-1,command=['/bin/true']))
            (p/'console.log').write_text('COMPLETE\n')
            recovered=flow.recover_tool(p,'COMPLETE')
            self.assertEqual(recovered['status'],'failed')

    def test_candidate_budget_and_names_fail_before_tool_launch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with self.assertRaises(ValueError):flow.candidate(root,{},'../escape',.6,'west_stack')
            for i in range(flow.BUDGET):flow.save(root/'candidates'/str(i)/'status.json',dict(status='failed'))
            with self.assertRaisesRegex(RuntimeError,'budget'):flow.candidate(root,{},'extra',.6,'west_stack')

    def test_resume_never_reuses_a_different_parameter_point(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);point=root/'candidates/u50'
            flow.save(point/'status.json',dict(status='failed'))
            flow.save(point/'experiment.json',dict(utilization=.5,layout='west_stack',congestion=False,geometry=dict(halo_um=26)))
            with self.assertRaisesRegex(RuntimeError,'parameters differ'):flow.candidate(root,{},'u50',.5,'south_row')
            self.assertEqual(json.loads((point/'status.json').read_text())['status'],'failed')

    def test_cell_accounting_excludes_physical_fill_and_io(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            script='''array set data {
 top.insts {logic fill io ram}
 logic.cell.name DFQD1BWP7T
 logic.cell.baseClass core
 logic.box {{0 0 2 1}}
 logic.name logic0
 fill.cell.name FILL64BWP7T
 fill.cell.baseClass core
 fill.box {{0 1 10 4}}
 fill.name fill0
 io.cell.name PDDW0204CDG
 io.cell.baseClass pad
 io.box {{0 0 60 115}}
 io.name io0
 ram.cell.name RA1SHD_2048x32M8
 ram.cell.baseClass block
 ram.box {{100 100 120 110}}
 ram.name model
}
proc dbGet {key} {global data;return $data($key)}
'''
            script+='source {'+str(flow.HERE/'cell_stats.tcl')+'}\ncapture_cell_stats final\n'
            run=subprocess.run(['tclsh'],cwd=d,input=script,text=True,capture_output=True)
            self.assertFalse(run.stderr)
            result=json.loads((Path(d)/'final_cells.json').read_text())
            self.assertEqual(result['active_standard_instances'],1)
            self.assertEqual(result['active_standard_area_um2'],2)
            self.assertEqual(result['macro_count'],1)
            self.assertEqual(result['macro_area_um2'],200)

    def test_pad_connection_survives_native_net_renaming(self):
        import subprocess
        script='''set moved 0
set bindings {}
proc addNet {name} {}
proc dbGet {args} {
 global moved
 if {[llength $args]==3} {return T0}
 switch -- [lindex $args 0] {
  T0.net {return N0}
  N0.instTerms {return P0}
  T0.net.name {return clk_i}
  P0.net.name {if {$moved} {return n_1} else {return clk_i}}
  default {error "unexpected query $args"}
 }
}
proc detachModulePort {args} {}
proc attachModulePort {args} {global moved;set moved 1}
proc attachTerm {args} {global bindings;lappend bindings $args}
'''
        script+='source {'+str(flow.HERE/'connect_pad.tcl')+'}\nconnect_signal_pad clk_i pad0 input\nputs [lindex $bindings 0]\nputs [lindex $bindings 1]\n'
        run=subprocess.run(['tclsh'],input=script,text=True,capture_output=True)
        self.assertFalse(run.stderr)
        self.assertEqual(run.stdout.splitlines(),['pad0 PAD clk_i','pad0 C n_1'])

    def test_release_summary_requires_both_sdf_and_independent_repeat(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rows={}
            for name,area in [('u50',10),('u60',8),('repeat',8)]:
                point=root/'candidates'/name;flow.save(point/'status.json',dict(status='complete'))
                rows[name]=dict(directory=str(point),eligible=True,die_area_mm2=area,setup_wns_ns=.3,hold_wns_ns=.02,wire_length_um=1000)
            flow.save(root/'baseline.json',dict(directory=str(root/'candidates/u50')))
            def read(point):return rows[point.name].copy()
            with patch('flow.metrics',side_effect=read):
                self.assertFalse(flow.summarize(root)['release_eligible'])
                for name in ('u50','u60'):
                    proof=root/'candidates'/name/'sdf'/'input';proof.parent.mkdir();proof.write_text('unit fixture')
                    flow.save(proof.parent/'result.json',dict(passed=True,sources_sha256={str(proof):flow.digest(proof)}))
                result=flow.summarize(root)
                self.assertTrue(result['release_eligible'])
                self.assertEqual(result['validated_area_improvement_percent'],20)
                (root/'candidates/u60/sdf/input').write_text('changed')
                self.assertFalse(flow.summarize(root)['release_eligible'])

if __name__=='__main__':unittest.main()
