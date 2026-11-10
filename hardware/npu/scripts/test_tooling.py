"""Unit tests for the verification / evaluation tooling (no simulator needed).

  python3 -m unittest discover -s hardware/npu/scripts -p 'test_*.py' -v

These guard the tools that produce sign-off evidence: a silent change in how a
build is configured, how results are counted, or a mutant that no longer
applies would weaken the evidence without failing any simulation.
"""
from pathlib import Path
import re
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'software/host'))

import deploy_envelope  # noqa: E402
import eval_change  # noqa: E402
import mutation_test  # noqa: E402
import sim_build  # noqa: E402


def defines(cmd):
    return {a[2:] for a in cmd if a.startswith('-D')}


class SimBuildTest(unittest.TestCase):
    PROFILE = {'SYNTHESIS', 'NPU_ARRAY_COLS=8', 'NPU_ENABLE_WAVE_TRACE=0', 'NPU_ENABLE_OP_TRACE=1', 'NPU_TILE=16'}

    def test_memory_configurations_are_pinned(self):
        # Changing any of these changes what every profiler and eval suite measures.
        self.assertEqual(defines(sim_build.program_tb_command('x', memory='behavioral')), self.PROFILE)
        self.assertEqual(defines(sim_build.program_tb_command('x', memory='fpga_mem')),
                         self.PROFILE | {'CHIPDESIGN_FPGA_MEM'})
        self.assertEqual(defines(sim_build.program_tb_command('x', memory='ra1shd')),
                         self.PROFILE | {'NPU_USE_RA1SHD_MODEL', 'NPU_USE_RA1SHD_ACTIVATION'})

    def test_ra1shd_adds_macro_model_and_all_sources_exist(self):
        cmd = sim_build.program_tb_command('x', memory='ra1shd')
        self.assertIn(sim_build.RA1SHD_MODEL, cmd)
        self.assertEqual(cmd[-1], sim_build.TESTBENCH)
        for m in sim_build.MEMORY:
            for s in sim_build.watched_sources(m):
                self.assertTrue((ROOT / s).is_file(), s)

    def test_unknown_memory_rejected(self):
        with self.assertRaises(ValueError):
            sim_build.program_tb_command('x', memory='fpga')

    def test_tile_and_non_profile_build(self):
        self.assertIn('NPU_TILE=8', defines(sim_build.program_tb_command('x', tile=8)))
        self.assertEqual(defines(sim_build.program_tb_command('x', profile=False)), set())


class RegressionCountTest(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / 'regression.log'
        p.write_text(text)
        return p

    def test_complete_run_counts_checks(self):
        p = self.write('PASS a\nPASS b\nPASS reject x\nALL 2 PROGRAM TESTS PASSED\n')
        self.assertEqual(eval_change.regression_counts(p), (3, 3))

    def test_interrupted_run_is_incomplete(self):
        self.assertEqual(eval_change.regression_counts(self.write('PASS a\nPASS b\n')), (2, 'INCOMPLETE'))

    def test_failure_counts_in_total(self):
        self.assertEqual(eval_change.regression_counts(self.write('PASS a\nFAIL b\n')), (1, 2))

    def test_missing_suite(self):
        self.assertEqual(eval_change.regression_counts(Path(tempfile.mkdtemp()) / 'none.log'), ('', ''))


class CoverageTest(unittest.TestCase):
    def test_aggregates_hits_and_tests_per_suite(self):
        src = Path(tempfile.mkdtemp())
        for case, n in (('c1', 3), ('c2', 0)):
            (src / 'regression' / case).mkdir(parents=True)
            (src / 'regression' / case / 'rtl.log').write_text(f'x\nCOVER op_conv {n}\nCOVER op_fc 1\nPASS\n')
        (src / 'regression' / 'nolog').mkdir()
        rows = {r['bin']: r for r in eval_change.coverage_rows('L', src)}
        self.assertEqual((rows['op_conv']['hits'], rows['op_conv']['tests_hit'], rows['op_conv']['tests_total']), (3, 1, 2))
        self.assertEqual(rows['op_fc']['tests_hit'], 2)

    def test_testbench_bin_names_are_unique_and_complete(self):
        tb = (ROOT / sim_build.TESTBENCH).read_text()
        nb = int(re.search(r'localparam NB=(\d+);', tb).group(1))
        names = re.findall(r'^\s+\d+: bin_name="(\w+)";', tb, re.M)
        self.assertEqual(len(names), nb)
        self.assertEqual(len(set(names)), nb)
        used = {int(i) for i in re.findall(r'hit\((\d+)\)', tb)}
        used |= {int(i) for g in re.findall(r'hit\(`CHK_CORE\.\w+ \? (\d+) : \(`CHK_CORE\.\w+ \? (\d+) : (\d+)\)\)', tb) for i in g}
        self.assertEqual(used, set(range(nb)), 'every coverage bin must be sampled somewhere')


class MutantTest(unittest.TestCase):
    def test_every_mutant_still_applies_exactly_once(self):
        # RTL edits that move a mutant's anchor would silently drop it from the score.
        ids = [m[0] for m in mutation_test.MUTANTS]
        self.assertEqual(len(ids), len(set(ids)))
        for mid, file, old, new, config, note in mutation_test.MUTANTS:
            text = (ROOT / file).read_text()
            self.assertEqual(text.count(old), 1, f'{mid}: anchor not unique in {file}')
            self.assertNotEqual(old, new, mid)
            self.assertTrue(note, mid)


class DeployClassifyTest(unittest.TestCase):
    def test_compiler_messages_map_to_constraints(self):
        c = deploy_envelope.classify
        self.assertEqual(c('model needs 9000 bytes'), 'model_bytes')
        self.assertEqual(c('activation capacity exceeded'), 'activation')
        self.assertEqual(c('too many physical descriptors'), 'descriptors')
        self.assertEqual(c('something new'), 'other')


if __name__ == '__main__':
    unittest.main()
