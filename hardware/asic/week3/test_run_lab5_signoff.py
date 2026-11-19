#!/usr/bin/env python3
"""Unit tests for fail-closed Calibre report parsing in run_lab5_signoff.py."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_lab5_signoff as lab5  # noqa: E402
import tapeout_1980_geometry as geom1980  # noqa: E402
import run_mmmc_tapeout_1980 as mmmc1980  # noqa: E402
import package_lab5_delivery as delivery  # noqa: E402


class Lab5ParserTests(unittest.TestCase):
    def _write(self, content: str) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / 'report.txt'
        p.write_text(content)
        return p

    def test_drc_rejects_malformed_or_missing_summary(self) -> None:
        with self.assertRaises(RuntimeError):
            lab5.parse_drc_rep(self._write('incomplete tool output without summary\n'))

    def test_drc_rejects_zero_rules_executed(self) -> None:
        p = self._write(
            'RULECHECK DRM.R.1 ...... TOTAL Result Count = 0 (0)\n'
            'TOTAL DRC RuleChecks Executed:   0\n'
            'TOTAL DRC Results Generated:     0 (0)\n'
        )
        with self.assertRaises(RuntimeError):
            lab5.parse_drc_rep(p)

    def test_drc_rejects_unreconciled_totals(self) -> None:
        p = self._write(
            'RULECHECK DRM.R.1 ...... TOTAL Result Count = 1 (1)\n'
            'TOTAL DRC RuleChecks Executed:   1\n'
            'TOTAL DRC Results Generated:     2 (2)\n'
        )
        with self.assertRaises(RuntimeError):
            lab5.parse_drc_rep(p)

    def test_drc_rejects_rule_count_mismatch(self) -> None:
        p = self._write(
            'RULECHECK M6.A.1 ....... TOTAL Result Count = 0 (0)\n'
            'TOTAL DRC RuleChecks Executed:   523\n'
            'TOTAL DRC Results Generated:     0 (0)\n'
        )
        with self.assertRaises(RuntimeError):
            lab5.parse_drc_rep(p)

    def test_drc_rejects_malformed_rulecheck_entry(self) -> None:
        p = self._write(
            'RULECHECK M6.A.1 ....... TOTAL Result Count = 0 (0)\n'
            'RULECHECK CO.R.4 ....... TOTAL Result Count = CORRUPT\n'
            'TOTAL DRC RuleChecks Executed:   523\n'
            'TOTAL DRC Results Generated:     0 (0)\n'
        )
        with self.assertRaises(RuntimeError):
            lab5.parse_drc_rep(p)

    def test_drc_rejects_duplicate_rulecheck_entry(self) -> None:
        p = self._write(
            'RULECHECK M6.A.1 ....... TOTAL Result Count = 0 (0)\n'
            'RULECHECK M6.A.1 ....... TOTAL Result Count = 0 (0)\n'
            'TOTAL DRC RuleChecks Executed:   2\n'
            'TOTAL DRC Results Generated:     0 (0)\n'
        )
        with self.assertRaises(RuntimeError):
            lab5.parse_drc_rep(p)

    def test_drc_accepts_reconciled_report(self) -> None:
        p = self._write(
            'RULECHECK M6.A.1 ....... TOTAL Result Count = 0 (0)\n'
            'RULECHECK DRM.R.1 ...... TOTAL Result Count = 1 (1)\n'
            'TOTAL DRC RuleChecks Executed:   2\n'
            'TOTAL DRC Results Generated:     1 (1)\n'
        )
        out = lab5.parse_drc_rep(p)
        self.assertEqual(out['rules_executed'], 2)
        self.assertEqual(out['total_hier'], 1)
        self.assertEqual(out['total_expanded'], 1)
        self.assertEqual(out['nonzero_rules'], {'DRM.R.1': {'hier': 1, 'expanded': 1}})

    def test_erc_rejects_failed_pathchk(self) -> None:
        p = self._write(
            'RULECHECK floating.psub_float ... TOTAL Result Count = 0 (0) FAILED PATHCHK IN LAYER DERIVATION\n'
            'TOTAL ERC RuleChecks Executed:   1\n'
            'TOTAL ERC RuleCheck Results Generated: 0 (0)\n'
        )
        with self.assertRaises(RuntimeError):
            lab5.parse_erc_sum(p)

    def test_erc_rejects_malformed_rulecheck_entry(self) -> None:
        p = self._write(
            'RULECHECK mnpg .......... TOTAL Result Count = 0 (0)\n'
            'RULECHECK npvss49 ....... MALFORMED\n'
            'TOTAL ERC RuleChecks Executed:   2\n'
            'TOTAL ERC RuleCheck Results Generated: 0 (0)\n'
        )
        with self.assertRaises(RuntimeError):
            lab5.parse_erc_sum(p)

    def test_lvs_parses_r_pr_deviations(self) -> None:
        p = self._write(
            '  #   #  # INCORRECT #\n'
            '  Error: Property errors.\n'
            '  Ports: 12 12 0 0\n'
            '  1 X1/R108(1.0,2.0) R(PR) Xi_pad/R_3 R\n'
            '    r: 3.40499e+06 ohm   r: 3.29071e+06 ohm   3.47%\n'
            '  45 45 0 0 R(PR)\n'
        )
        out = lab5.parse_lvs_rep(p)
        self.assertEqual(out['status'], 'INCORRECT')
        self.assertEqual(out['property_error_count'], 45)
        self.assertAlmostEqual(out['max_io_resistor_deviation_pct'], 3.47)


MOCK_SIZES = {
    'RA1SHD_2048x32M8': [554.595, 723.850],
    'PCORNER': [115.0, 115.0],
    'PDDW0204CDG': [60.0, 115.0],
    'PVSS1CDG': [60.0, 115.0],
    'PVSS2CDG': [60.0, 115.0],
    'PVDD1CDG': [60.0, 115.0],
    'PVDD2CDG': [60.0, 115.0],
    'PVDD2POC': [60.0, 115.0],
    'PAD60LU_OBV': [60.0, 73.16],
}


class Tapeout1980Tests(unittest.TestCase):
    def test_fixed_sealring_1980_exact_box_and_grid(self) -> None:
        # 1949.600 x 1949.600 um raw die at 2000 DBU/um = 3899200 DBU
        box = lab5.compute_sealring_box([0, 0, 3899200, 3899200], dbu_per_um=2000, max_outer_um=1980.0, fixed_outer_um=1980.0)
        self.assertEqual(box['sealed_width_um'], 1980.0)
        self.assertEqual(box['sealed_height_um'], 1980.0)
        rx0, ry0, rx1, ry1 = box['ref_dbu']
        self.assertEqual(rx1 - rx0, 3900000)
        self.assertEqual(ry1 - ry0, 3900000)
        self.assertLessEqual(rx0, 0)
        self.assertGreaterEqual(rx1, 3899200)
        self.assertEqual(rx0 % 10, 0)
        self.assertEqual(ry0 % 10, 0)
        ox0, oy0, ox1, oy1 = box['outer_dbu']
        self.assertEqual(ox1 - ox0, 3960000)
        self.assertEqual(oy1 - oy0, 3960000)

    def test_fixed_sealring_rejects_oversized_raw_die(self) -> None:
        with self.assertRaises(RuntimeError):
            lab5.compute_sealring_box([0, 0, 3900200, 3900000], dbu_per_um=2000, max_outer_um=1980.0, fixed_outer_um=1980.0)

    def test_fixed_die_geometry_core7t_snapping_and_pads(self) -> None:
        geo = geom1980.compute_fixed_die_geometry(MOCK_SIZES, target_sealed_um=1980.0)
        self.assertAlmostEqual(geo['core_w_um'], 1649.200, places=3)
        self.assertAlmostEqual(geo['core_h_um'], 1646.400, places=3)
        self.assertAlmostEqual(geo['margin_x_um'], 150.200, places=3)
        self.assertAlmostEqual(geo['margin_y_um'], 151.600, places=3)
        self.assertAlmostEqual(geo['die_w_um'], 1949.600, places=3)
        self.assertAlmostEqual(geo['die_h_um'], 1949.600, places=3)
        self.assertAlmostEqual(geo['core_w_um'] / 0.560, round(geo['core_w_um'] / 0.560), places=6)
        self.assertAlmostEqual(geo['core_h_um'] / 3.920, round(geo['core_h_um'] / 3.920), places=6)
        self.assertEqual(len(geo['pads']), 35)
        self.assertEqual(len(geo['macros']), 2)

    def test_write_config_tcl_emits_strict_1980_params(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'config.tcl'
            paths = dict(
                std_lib='/pdk/std.lib', io_lib='/pdk/io.lib', macro_lib='/pdk/sram.lib',
                std_lef='/pdk/std.lef', io_lef='/pdk/io.lef', io_ant_lef='/pdk/io_ant.lef',
                bond_lef='/pdk/bond.lef', macro_lef='/pdk/sram.lef', macro_ant_lef='/pdk/sram_ant.lef',
                captable='/pdk/typ.captable', gds_map='/pdk/gds2.map',
                std_gds='/pdk/std.gds', io_gds='/pdk/io.gds', bond_gds='/pdk/bond.gds', macro_gds='/pdk/sram.gds',
                opt_dir='/repo/optimize', flow_dir='/repo/flow', source_root='/repo',
                netlist='/run/mapped.v', sdc='/run/mapped.sdc',
                std_dir='/pdk/std', io_dir='/pdk/io', sram_dir='/pdk/sram',
            )
            mmmc1980.write_config_tcl(cfg, paths, MOCK_SIZES)
            txt = cfg.read_text()
            self.assertIn('set core_w_um 1649.2', txt)
            self.assertIn('set core_h_um 1646.4', txt)
            self.assertIn('set die_side_um 1949.6', txt)
            self.assertIn('set corner_um 115.0', txt)
            self.assertIn('set rc_setup_scale 1.15', txt)
            self.assertIn('set rc_hold_scale 0.8', txt)

    def test_package_lab5_delivery_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pnr = root / 'pnr'
            lab = root / 'lab5'
            out = root / 'delivery'
            (pnr / 'lab4_exports').mkdir(parents=True)
            (pnr / 'lab4_exports' / 'my_soc_top.def').write_text('VERSION 5.8 ;\n')
            (pnr / 'mmmc_corners.csv').write_text('corner,mode,wns\nss125,setup,0.5\n')
            (lab / 'work').mkdir(parents=True)
            (lab / 'delivery').mkdir(parents=True)
            gds_bytes = b'FAKE_POSTDUMMY_GDS_PAYLOAD'
            cdl_bytes = b'* FAKE CDL\n.SUBCKT my_soc_top\n.ENDS\n'
            # Place copies in both work/ and delivery/ to verify non-ambiguous selection
            for sub in ('work', 'delivery'):
                (lab / sub / 'postDummy.gds').write_bytes(gds_bytes)
                (lab / sub / 'my_soc_top.cdl').write_bytes(cdl_bytes)
            sha_gds = hashlib.sha256(gds_bytes).hexdigest()
            sha_cdl = hashlib.sha256(cdl_bytes).hexdigest()
            (lab / 'delivery' / 'final_inputs.sha256').write_text(
                f'{sha_gds}  postDummy.gds\n{sha_cdl}  my_soc_top.cdl\n'
            )
            (lab / 'work' / 'drc.rpt').write_text('TOTAL DRC Results Generated: 0 (0)\n')
            manifest_out = root / 'zero_pdk_manifest.json'
            manifest = delivery.package_delivery(pnr, lab, out, zero_pdk_manifest=manifest_out)
            self.assertTrue(manifest['verification']['gzip'])
            self.assertTrue(manifest['verification']['sha256sums'])
            self.assertTrue(manifest['verification']['final_inputs'])
            self.assertTrue((out / 'design_and_reports.tar.gz').is_file())
            subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=out, check=True, capture_output=True)
            m_txt = manifest_out.read_text()
            self.assertNotIn(str(root), m_txt)
            parsed = json.loads(m_txt)
            self.assertEqual(len(parsed['files']), 4)

    def test_verify_checksums_rejects_empty_or_incomplete_final_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'postDummy.gds').write_bytes(b'GDS')
            (root / 'my_soc_top.cdl').write_bytes(b'CDL')
            empty_f = root / 'empty.sha256'
            empty_f.write_text('\n')
            with self.assertRaises(RuntimeError):
                delivery.verify_checksums(root, empty_f, required=('postDummy.gds', 'my_soc_top.cdl'))
            partial_f = root / 'partial.sha256'
            partial_f.write_text(f"{hashlib.sha256(b'GDS').hexdigest()}  postDummy.gds\n")
            with self.assertRaises(RuntimeError):
                delivery.verify_checksums(root, partial_f, required=('postDummy.gds', 'my_soc_top.cdl'))


if __name__ == '__main__':
    unittest.main()
