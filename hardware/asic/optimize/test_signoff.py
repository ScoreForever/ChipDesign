from pathlib import Path
import tempfile
import unittest
import signoff

DONE = dict(status='complete')


class SignoffTests(unittest.TestCase):
    def test_deck_rewrite_replaces_only_named_statements_once(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)/'a.drc'; dst = Path(d)/'b.drc'
            src.write_text('#DEFINE 1.8V\nLAYOUT PATH "GDSFILENAME"\nLAYOUT PRIMARY "TOP"\nLAYOUT PRIMARY "DUP"\nLAYOUT SYSTEM GDSII\nRULE A {}\n')
            signoff.rewrite_deck(src, dst, {'LAYOUT PATH': 'LAYOUT PATH "chip.gds"', 'LAYOUT PRIMARY': 'LAYOUT PRIMARY "my_soc_top"'})
            text = dst.read_text()
            self.assertIn('LAYOUT PATH "chip.gds"', text); self.assertIn('LAYOUT SYSTEM GDSII', text)
            self.assertEqual(text.count('LAYOUT PRIMARY'), 1); self.assertIn('#DEFINE 1.8V', text)
            with self.assertRaisesRegex(RuntimeError, 'lacks'):
                signoff.rewrite_deck(src, dst, {'SOURCE PATH': 'SOURCE PATH "x"'})

    def test_parsers_require_positive_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            for check in signoff.CHECKS:  # nothing written: every check fails
                self.assertFalse(signoff.evaluate(check, out, DONE)['passed'])
            (out/'drc.rep').write_text('RULECHECK M1.S.1 ....... TOTAL Result Count = 3 (3)\nTOTAL DRC Results Generated:   3 (3)\n')
            r = signoff.evaluate('drc', out, DONE)
            self.assertFalse(r['passed']); self.assertEqual(r['violated_rules'], {'M1.S.1': 3})
            (out/'drc.rep').write_text('RULECHECK M1.S.1 ....... TOTAL Result Count = 0 (0)\nTOTAL DRC Results Generated:   0 (0)\n')
            self.assertTrue(signoff.evaluate('drc', out, DONE)['passed'])
            self.assertFalse(signoff.evaluate('drc', out, dict(status='failed'))['passed'])
            (out/'lvs.rep').write_text('               ##################\n               #                #\n               #    CORRECT     #\n               #                #\n               ##################\n')
            self.assertTrue(signoff.evaluate('lvs', out, DONE)['passed'])
            (out/'lvs.rep').write_text('   #     #   INCORRECT   #\n')
            self.assertFalse(signoff.evaluate('lvs', out, DONE)['passed'])
            (out/'console.log').write_text('PT_SETUP_WNS 1.25\nPT_HOLD_WNS -0.010\n')
            self.assertFalse(signoff.evaluate('sta', out, DONE)['passed'])
            (out/'console.log').write_text('PT_SETUP_WNS 1.25\nPT_HOLD_WNS 0.031\nLEC_RESULT 1\nVerification SUCCEEDED\n')
            self.assertTrue(signoff.evaluate('sta', out, DONE)['passed'])
            self.assertTrue(signoff.evaluate('lec', out, DONE)['passed'])

    def test_only_declared_codes_are_accepted(self):
        import flow, hashlib, json
        with tempfile.TemporaryDirectory() as d:
            out = Path(d); cmd = ['bash', 'run.sh']
            receipt = dict(exit_code=0, finished=1.0, command_sha256=hashlib.sha256(json.dumps(cmd).encode()).hexdigest())
            (out/'tool_exit.json').write_text(json.dumps(receipt))
            (out/'console.log').write_text('Error: Library Compiler executable path is not set. (PT-063)\nDONE\n')
            state = flow.finish_tool(out, 'DONE', dict(command=cmd, accepted_codes=['PT-063']))
            self.assertEqual(state['status'], 'complete'); self.assertEqual(len(state['accepted_diagnostics']), 1)
            self.assertEqual(flow.finish_tool(out, 'DONE', dict(command=cmd))['status'], 'failed')
            (out/'console.log').write_text('Error: Library Compiler executable path is not set. (PT-063)\nError: other (SDC-5)\nDONE\n')
            self.assertEqual(flow.finish_tool(out, 'DONE', dict(command=cmd, accepted_codes=['PT-063']))['status'], 'failed')

    def test_precision_rescales_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            deck = Path(d)/'r.drc'; deck.write_text('PRECISION    1000\nRESOLUTION     5   //grid\n')
            self.assertEqual(signoff.precision(deck), {'PRECISION': 'PRECISION 2000', 'RESOLUTION': 'RESOLUTION 10'})

    def test_poc_label_uses_south_pad(self):
        with tempfile.TemporaryDirectory() as d:
            cand = Path(d); (cand/'config.tcl').write_text('set pads {\n {i_pad_S_poc PVDD2POC - supply S 1717.275 0 R0}\n}\n')
            self.assertEqual(signoff.poc_label(cand), 'LAYOUT TEXT "POC" 1747.275 113.215 42 "my_soc_top"')

    def test_lvs_accepts_only_io_resistor_values(self):
        head = 'CELL COMPARISON RESULTS ( TOP LEVEL )\n #   INCORRECT   #\n  Error:    Property errors.\nLAYOUT CELL NAME: my_soc_top\n'
        rule = '*'*40+'\n'
        table = ('PROPERTY ERRORS\nDISC#  LAYOUT  SOURCE  ERROR\n'+rule+
                 '  1    X16/X2/X0/R108(1885.6900,1675.7750)  R(PR)                Xi_pad_E_poc/R_3  R\n'
                 '       r: 3.40499e+06 ohm                                        r: 3.29071e+06 ohm          3.47%\n'+rule+'  INFORMATION\n')
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out/'lvs.rep').write_text(head+table)
            r = signoff.evaluate('lvs', out, DONE)
            self.assertTrue(r['passed']); self.assertEqual(r['accepted_io_resistor_errors'], 1)
            (out/'lvs.rep').write_text(head.replace('Property errors.', 'Property errors.\n  Error:    Connectivity errors.')+table)
            self.assertFalse(signoff.evaluate('lvs', out, DONE)['passed'])
            (out/'lvs.rep').write_text(head+table.replace('Xi_pad_E_poc/R_3', 'Xi_core/R_3'))
            self.assertFalse(signoff.evaluate('lvs', out, DONE)['passed'])
            (out/'lvs.rep').write_text(head+table.replace('3.47%', '9.10%'))
            self.assertFalse(signoff.evaluate('lvs', out, DONE)['passed'])
            mos = '  2    X1/M3(1.0,2.0)  MN(N)  Xi_core/M_3  MN\n       w: 1u  w: 2u  l: 0.18u  l: 0.18u\n'
            (out/'lvs.rep').write_text(head+table.replace(rule+'  INFORMATION', mos+rule+'  INFORMATION'))
            self.assertFalse(signoff.evaluate('lvs', out, DONE)['passed'])

    def test_drc_waivers_are_narrow(self):
        self.assertTrue(signoff.drc_waiver('DRM.R.1', {}))
        self.assertTrue(signoff.drc_waiver('ESD.25g', {'io_ring': 3}))
        self.assertTrue(signoff.drc_waiver('LUP.5.3g_3.3V', {'io_ring': 1}))
        self.assertTrue(signoff.drc_waiver('CO.R.4', {'io_ring': 1}))
        self.assertIsNone(signoff.drc_waiver('ESD.25g', {'io_ring': 3, 'core': 1}))
        self.assertIsNone(signoff.drc_waiver('M5.R.1', {'core': 1}))
        self.assertIsNone(signoff.drc_waiver('M2.S.1', {'io_ring': 1}))

    def test_drc_locations_use_centroids(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d)/'drc.db'
            db.write_text('my_soc_top 2000\nA.1\n2 2 1 date\nA.1 { @ text }\n'
                          'p 1 4\n0 0\n200 0\n200 200\n0 200\n'
                          'p 2 4\n2000000 2000000\n2000200 2000000\n2000200 2000200\n2000000 2000200\n')
            self.assertEqual(dict(signoff.drc_locations(db, (2000, 115))['A.1']), {'io_ring': 1, 'core': 1})


if __name__ == '__main__':
    unittest.main()
