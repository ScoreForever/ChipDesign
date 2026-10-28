import unittest
import postlayout

MODEL = ('module FA1D0BWP7T (A,B,CI,S,CO);\n specify\n if (B==1) (A => S)=(0,0);\n'
         ' ifnone (A => S) = (0, 0);\n endspecify\nendmodule\nmodule INVD1BWP7T (I,ZN);\nendmodule\n')
NEP = 'xmelab: *W,SDFNEP: Unable to annotate to non-existent path (IOPATH (posedge %s) %s) of instance x.y of module %s <f, line 1>.\n'
PERIOD = ('xmelab: *W,SDFNET: Unable to annotate to non-existent timing check (PERIOD (negedge CLK) '
          '(1.6)) of instance y of module RA1SHD_2048x32M8 <f>.\n')


class PostlayoutTests(unittest.TestCase):
    def test_ifnone_arcs_are_read_from_models(self):
        self.assertEqual(postlayout.ifnone_arcs(MODEL), {'FA1D0BWP7T': {('A', 'S')}, 'INVD1BWP7T': set()})

    def test_sram_period_only_posedge_below_core_period(self):
        self.assertTrue(postlayout.sram_period_ok('$period(posedge CLK, 3.000, N);'))
        self.assertFalse(postlayout.sram_period_ok('$period(posedge CLK, 25.0, N);'))
        self.assertFalse(postlayout.sram_period_ok('$period(negedge CLK, 3, N); $period(posedge CLK, 3, N);'))

    def test_only_audited_diagnostics_are_accepted(self):
        arcs = postlayout.ifnone_arcs(MODEL)
        ok, d = postlayout.classify(NEP % ('A', 'S', 'FA1D0BWP7T') + PERIOD, arcs, True)
        self.assertTrue(ok); self.assertEqual(d['accepted_ifnone_paths']['by_arc'], {'FA1D0BWP7T A->S': 1})
        self.assertFalse(postlayout.classify(NEP % ('I', 'ZN', 'INVD1BWP7T'), arcs, True)[0])
        self.assertFalse(postlayout.classify(PERIOD, arcs, False)[0])
        self.assertFalse(postlayout.classify('xmelab: *W,SDFNET: x (RECREM (posedge CDN) (posedge CP)) of z\n', arcs, True)[0])
        self.assertFalse(postlayout.classify('Warning!  Timing violation\n', arcs, True)[0])
        self.assertFalse(postlayout.classify('xmelab: *W,SDFINF: Instance X not found\n', arcs, True)[0])
        self.assertFalse(postlayout.classify('xmelab: *W,MXWARN: Reached maximum warning limit\n', arcs, True)[0])

    def test_sram_clk_q_rewrite_is_narrow(self):
        sdf = ('(CELL\n (CELLTYPE "RA1SHD_2048x32M8")\n (DELAY (ABSOLUTE\n\t(IOPATH (posedge CLK) Q[3] (1) (1))\n))\n)\n'
               '(CELL\n (CELLTYPE "DFQD1BWP7T")\n (DELAY (ABSOLUTE\n\t(IOPATH (posedge CP) Q (1) (1))\n))\n)\n')
        text, n = postlayout.sram_clk_q_noedge(sdf, '(CLK => Q[0])=(1,1);')
        self.assertEqual(n, 1); self.assertIn('(IOPATH CLK Q[3]', text); self.assertIn('(IOPATH (posedge CP) Q', text)
        with self.assertRaises(RuntimeError):
            postlayout.sram_clk_q_noedge(sdf, '(posedge CLK => Q[0])=(1,1);')

    def test_only_time0_input_pad_conflicts_are_accepted(self):
        base = dict(status='failed', exit_code=0, marker=True)
        ok = ['ERROR :                    0 ++BUS CONFLICT++ : tb_fullchip.dut.i_pad_%s' % p for p in ('clk_i', 'td_i')]
        self.assertEqual(postlayout.accept_time0_pad_conflicts(dict(base, errors=ok))[0]['status'], 'complete')
        late = ['ERROR :                 1250 ++BUS CONFLICT++ : tb_fullchip.dut.i_pad_clk_i']
        self.assertEqual(postlayout.accept_time0_pad_conflicts(dict(base, errors=late))[0]['status'], 'failed')
        out = ['ERROR :                    0 ++BUS CONFLICT++ : tb_fullchip.dut.i_pad_td_o']
        self.assertEqual(postlayout.accept_time0_pad_conflicts(dict(base, errors=out))[0]['status'], 'failed')
        mixed = ok + ['**ERROR: something else']
        self.assertEqual(postlayout.accept_time0_pad_conflicts(dict(base, errors=mixed))[0]['status'], 'failed')
        self.assertEqual(postlayout.accept_time0_pad_conflicts(dict(base, errors=ok, marker=False))[0]['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
