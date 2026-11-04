"""Offline checks for the board-suite command sequence and fixtures."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('nocpu_board_suite', HERE/'nocpu_board_suite.py')
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


class Tests(unittest.TestCase):
    def test_error_code_is_exact(self):
        def fail(): raise RuntimeError('NPU error 18: descriptor')
        self.assertIn('error 18', suite.expect_error(fail, 18))
        with self.assertRaises(RuntimeError): suite.expect_error(fail, 1)
        with self.assertRaises(AssertionError): suite.expect_error(lambda: None, 18)

    def test_upload_sequence_and_endianness(self):
        class Fake:
            def __init__(self): self.calls=[]
            def command(self,*args): self.calls.append(args)
            def write(self,*args): self.calls.append(args)
        m={'model_bytes':4,'op_count':1,'input_base':0,'input_bytes':1,'output_base':4,'output_bytes':4,'input_dtype':'i4'}
        fake=Fake()
        suite.raw_upload(fake, suite.Package(bytes([1,2,3,4]),m))
        self.assertEqual(fake.calls[0],(suite.Op.MODEL_BEGIN,))
        self.assertEqual(fake.calls[-1],(0x10000,0x04030201))
        self.assertNotIn((suite.Op.MODEL_COMMIT,),fake.calls)

    def test_fixture_compilation_and_expected_rejection(self):
        for name, model, frame in suite.fixtures():
            for cols in (4,8):
                for fuse in (True,False):
                    with self.subTest(name=name,cols=cols,fuse=fuse):
                        if name=='tinycnn' and not fuse:
                            with self.assertRaisesRegex(ValueError,'activation capacity exceeded'):
                                suite.compile_model(model,cols=cols,fuse=fuse)
                        else:
                            pkg=suite.compile_model(model,cols=cols,fuse=fuse)
                            result=suite.reference(model,frame)[model['nodes'][-1]['output']]
                            self.assertEqual(len(suite.encoded(result,pkg.manifest['output_dtype'])),pkg.manifest['output_bytes'])

    def test_bad_hash_records_failure_and_no_connection(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);(path/'design.bit').write_bytes(b'bit')
            args=SimpleNamespace(output=path/'out',bitstream=path/'design.bit',sha256='0'*64,
                                 provenance=path/'none',host='localhost',port=6666,repeat=1,timeout=1)
            with patch.object(suite,'TclRpc',side_effect=AssertionError('unexpected connection')):
                self.assertEqual(suite.execute(args),1)
            report=json.loads((path/'out/report.json').read_text())
            self.assertFalse(report['passed'])
            self.assertIn('SHA-256 mismatch',report['error'])
            self.assertTrue(report['manual_pending'])

if __name__=='__main__': unittest.main()
