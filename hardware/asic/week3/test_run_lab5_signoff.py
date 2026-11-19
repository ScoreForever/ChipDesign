#!/usr/bin/env python3
"""Unit tests for fail-closed Calibre report parsing in run_lab5_signoff.py."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_lab5_signoff as lab5  # noqa: E402


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
            'TOTAL DRC RuleChecks Executed:   523\n'
            'TOTAL DRC Results Generated:     2 (2)\n'
        )
        with self.assertRaises(RuntimeError):
            lab5.parse_drc_rep(p)

    def test_drc_accepts_reconciled_report(self) -> None:
        p = self._write(
            'RULECHECK M6.A.1 ....... TOTAL Result Count = 0 (0)\n'
            'RULECHECK DRM.R.1 ...... TOTAL Result Count = 1 (1)\n'
            'TOTAL DRC RuleChecks Executed:   523\n'
            'TOTAL DRC Results Generated:     1 (1)\n'
        )
        out = lab5.parse_drc_rep(p)
        self.assertEqual(out['rules_executed'], 523)
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


if __name__ == '__main__':
    unittest.main()
