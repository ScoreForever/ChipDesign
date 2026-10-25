import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import flow

SIZES={'RA1SHD_2048x32M8':[554.595,723.85],'PCORNER':[114.84,114.84],
       'PDDW0204CDG':[80,114.84],**{cell:[80,114.84] for _,cell in flow.SUPPLIES}}

class FlowTests(unittest.TestCase):
    def test_geometry_preserves_macro_clearance_and_all_seven_signals(self):
        a=flow.geometry(3153049,SIZES,.5)
        b=flow.geometry(3153049,SIZES,.6,(.8,.2))
        self.assertLess(b['die_side_um'],a['die_side_um'])
        self.assertEqual(len([p for p in a['pads'] if p['port']]),7)
        x,y,w,h=b['macro'];margin=b['margin_um'];core=b['core_side_um']
        self.assertGreater(x,margin+26);self.assertLess(x+w,margin+core-26)
        self.assertGreater(y,margin+26);self.assertLess(y+h,margin+core-26)
        self.assertEqual({p['side'] for p in a['pads']},{'N','S','E','W'})
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
            macro=base/'macro.lib';macro.write_text('cell (RA1SHD_2048x32M8) { area : 400; }')
            (source/'console.log').write_text('EDA_MAPPED_SYNTHESIS_COMPLETE\nError : failed\n')
            with self.assertRaises(RuntimeError):flow.adopt_synthesis(output,{'macro_lib':macro},source)
            (source/'console.log').write_text('EDA_MAPPED_SYNTHESIS_COMPLETE\n')
            for name in ('run.sh','run.tcl','my_soc_top.v','my_soc_top.sdc','timing.rpt'):(source/name).write_text('retained source')
            (source/'my_soc_top.sdc').write_text('set_units -time ns\ncreate_clock -name core_clk -period 20 [get_ports clk_i]\ncreate_clock -name jtag_clk -period 1000 [get_ports tck_i]\n')
            (source/'exit_code').write_text('0')
            (source/'area.rpt').write_text('my_soc_top 123 1500.0 200.0\n')
            flow.adopt_synthesis(output,{'macro_lib':macro},source)
            area=json.loads((output/'synthesis/area.json').read_text())
            self.assertEqual(area['standard_cell_area_um2'],1100)
            self.assertEqual((output/'synthesis/mapped.v').read_bytes(),(source/'my_soc_top.v').read_bytes())
            with self.assertRaises(RuntimeError):flow.adopt_synthesis(output,{'macro_lib':macro},source)

    def test_clock_units_are_never_silently_reinterpreted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.sdc'
            p.write_text('set_units -time ps\ncreate_clock -name core_clk -period 20000\ncreate_clock -name jtag_clk -period 1000000\n')
            with self.assertRaises(RuntimeError):flow.verify_clocks(p)
            p.write_text('set_units -time ns\ncreate_clock -name core_clk -period 20\ncreate_clock -name jtag_clk -period 1000\n')
            self.assertEqual(flow.verify_clocks(p)['core_clk'],20)

if __name__=='__main__':unittest.main()
