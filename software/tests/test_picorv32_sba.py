"""Host loader behavior with a mocked SBA, not a physical JTAG test."""
from pathlib import Path
import tempfile
import unittest
import tkinter

ROOT = Path(__file__).resolve().parents[2]


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tcl = tkinter.Tcl()
        self.tcl.call('source', str(ROOT / 'software/tools/picorv32_sba.tcl'))
        self.tcl.eval('''
            set memory [dict create]
            set writes 0
            set corrupt 0
            set engine_busy 0
            proc echo {message} {}
            proc pico_sba_write {address data} {
                incr ::writes
                dict set ::memory [expr {$address}] [expr {$data}]
                if {$address == 0x1003c} { dict set ::memory 65592 [expr {$data ? 4 : 1}] }
            }
            proc pico_sba_read {address} {
                set address [expr {$address}]
                if {$address == 0x70000410} { return $::engine_busy }
                if {$::corrupt && $address == 0x80000000} { return 0 }
                if {[dict exists $::memory $address]} { return [dict get $::memory $address] }
                return 0
            }
        ''')
        (ROOT / 'work').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='pico-loader-test-', dir=ROOT / 'work')
        self.addCleanup(self.temporary.cleanup)
        self.image = Path(self.temporary.name) / 'image.hex'

    def load(self, text):
        self.image.write_text(text, encoding='ascii')
        return self.tcl.call('pico_load_hex', str(self.image))

    def test_load_verify_then_run(self):
        self.load('// image\n800002b7\n00028067\n')
        self.assertEqual(int(self.tcl.eval('dict get $memory 2147483648')), 0x800002b7)
        self.assertEqual(int(self.tcl.eval('dict get $memory 65596')), 1)

    def test_invalid_image_does_not_stop_or_write(self):
        for text in ['001100', '@0000\n00000013', '00000013\n' * 2049]:
            with self.assertRaises(tkinter.TclError):
                self.load(text)
        self.assertEqual(int(self.tcl.eval('set writes')), 0)

    def test_bad_readback_keeps_cpu_reset(self):
        self.tcl.eval('set corrupt 1')
        with self.assertRaisesRegex(tkinter.TclError, 'verification failed'):
            self.load('800002b7\n')
        self.assertEqual(int(self.tcl.eval('dict get $memory 65596')), 0)

    def test_busy_dma_keeps_cpu_reset_and_sram_unchanged(self):
        self.tcl.eval('set engine_busy 1')
        with self.assertRaisesRegex(tkinter.TclError, 'still busy'):
            self.load('800002b7\n')
        self.assertEqual(int(self.tcl.eval('dict get $memory 65596')), 0)
        self.assertEqual(int(self.tcl.eval('dict exists $memory 2147483648')), 0)


if __name__ == '__main__':
    unittest.main()
