"""CLI contract for board evidence review."""
import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / 'nocpu_evidence_check.py'

class EvidenceCLI(unittest.TestCase):
    def test_missing_expected_cases_cannot_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'report.json').write_text(json.dumps({'passed': True, 'cases': [], 'manual_pending': []}))
            result = subprocess.run([sys.executable, str(TOOL), str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('incomplete', result.stdout.lower())

    def test_tampered_actual_output_fails_review(self):
        names = ('cnn', 'pool_first', 'mlp', 'i32_pool', 'strided', 'edges', 'long_k', 'tinycnn', 'host_i32_quant', 'host_i32_pool', 'tiny_padded', 'one_mac', 'negative_pool', 'full_activation', 'full_activation_8k', 'upper_activation', 'slot_pressure')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cases = []
            good_hash = hashlib.sha256(b'good').hexdigest()
            for name in names:
                for mode in ('fused', 'plain'):
                    case = f'{name}_{mode}'
                    if case == 'tinycnn_plain':
                        cases.append({'name':case, 'status':'EXPECTED_HOST_REJECTION'})
                        continue
                    folder = root/case
                    folder.mkdir()
                    (folder/'actual_001.bin').write_bytes(b'tampered')
                    (folder/'expected_001.bin').write_bytes(b'good')
                    (folder/'input_001.bin').write_bytes(b'input')
                    cases.append({'name':case, 'status':'PASS', 'runs':[{'matches':True, 'output_sha256':good_hash}]})
            (root/'report.json').write_text(json.dumps({'passed':True, 'cases':cases, 'manual_pending':[]}))
            result = subprocess.run([sys.executable, str(TOOL), str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('mismatch', result.stdout.lower())

    def test_claimed_pass_without_negative_cases_is_incomplete(self):
        names = ('cnn', 'pool_first', 'mlp', 'i32_pool', 'strided', 'edges', 'long_k', 'tinycnn', 'host_i32_quant', 'host_i32_pool', 'tiny_padded', 'one_mac', 'negative_pool', 'full_activation', 'full_activation_8k', 'upper_activation', 'slot_pressure')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cases = []
            for name in names:
                for mode in ('fused', 'plain'):
                    case = f'{name}_{mode}'
                    if case == 'tinycnn_plain':
                        cases.append({'name': case, 'status': 'EXPECTED_HOST_REJECTION'})
                        continue
                    folder = root/case
                    folder.mkdir()
                    (folder/'actual_001.bin').write_bytes(b'good')
                    (folder/'expected_001.bin').write_bytes(b'good')
                    (folder/'input_001.bin').write_bytes(b'input')
                    h = lambda b: hashlib.sha256(b).hexdigest()
                    cases.append({'name':case, 'status':'PASS', 'runs':[{'matches':True,'output_sha256':h(b'good'),'expected_sha256':h(b'good'),'input_sha256':h(b'input')}]})
            (root/'report.json').write_text(json.dumps({'passed':True,'cases':cases,'manual_pending':[]}))
            result = subprocess.run([sys.executable, str(TOOL), str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('negative', result.stdout.lower())

    def test_manual_pending_does_not_claim_complete(self):
        names = ('cnn', 'pool_first', 'mlp', 'i32_pool', 'strided', 'edges', 'long_k', 'tinycnn', 'host_i32_quant', 'host_i32_pool', 'tiny_padded', 'one_mac', 'negative_pool', 'full_activation', 'full_activation_8k', 'upper_activation', 'slot_pressure')
        errors = {'start_without_model':5, 'input_commit_without_begin':5, 'start_without_input':5, 'incomplete_input_commit':7, 'input_out_of_order':6, 'incomplete_model_commit':7, 'illegal_descriptor_opcode':18, 'reserved_word':18, 'invalid_stride':18, 'invalid_shape':19, 'invalid_destination':20, 'unknown_source':21, 'start_before_ack':9, 'input_before_ack':9}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); cases = []
            h = lambda b: hashlib.sha256(b).hexdigest()
            for name in names:
                for mode in ('fused', 'plain'):
                    case = f'{name}_{mode}'
                    if case == 'tinycnn_plain':
                        cases.append({'name':case, 'status':'EXPECTED_HOST_REJECTION'}); continue
                    folder = root/case; folder.mkdir()
                    (folder/'actual_001.bin').write_bytes(b'good'); (folder/'expected_001.bin').write_bytes(b'good'); (folder/'input_001.bin').write_bytes(b'input')
                    cases.append({'name':case, 'status':'PASS', 'runs':[{'matches':True,'output_sha256':h(b'good'),'expected_sha256':h(b'good'),'input_sha256':h(b'input')}]})
            negatives = [{'name':name,'expected_error':code,'status':'PASS','actual':f'NPU error {code}: test'} for name,code in errors.items()]
            (root/'report.json').write_text(json.dumps({'passed':True,'cases':cases,'negative_cases':negatives,'manual_pending':['physical reset']}))
            result = subprocess.run([sys.executable, str(TOOL), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('manual', result.stdout.lower())

if __name__ == '__main__': unittest.main()
