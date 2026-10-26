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


if __name__ == '__main__':
    unittest.main()
