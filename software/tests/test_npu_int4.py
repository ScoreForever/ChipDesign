"""Native INT4 package, byte transport and explicit PTQ boundary checks."""
import copy
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from npu_program import (ABI, NUMERIC_FORMAT, FORMAT_MAGIC, Package, compile_model,
                         pack_i4, unpack_i4, encode_tensor, decode_tensor,
                         quantize_int8_model, requant)
from nocpu_jtag import Npu, Op


def model():
    return dict(input=dict(name='x', shape=[1, 1, 3]), nodes=[
        dict(op='FC', input='x', output='a', channels=1, weights=[-8, 7, -1], bias=[123]),
        dict(op='Requant', input='a', output='y', multiplier=[0x4000], shift=[-2])])


class Transport:
    def __init__(self, cols=8, numeric=NUMERIC_FORMAT):
        self.rows=[]
        self.regs={0:ABI, 4:10, 0x50:8192, 0x54:8192, 0x58:(4<<16)|cols,
                   0x5c:1024, 0x60:16, 0x6c:numeric}

    def exchange(self, op, addr=0, data=0):
        self.rows.append((op, addr, data))
        if op == Op.WRITE:self.regs[addr]=data
        if op == Op.READ:return self.regs.get(addr, 0)
        return 0


class Int4Tests(unittest.TestCase):
    def test_wire_encoding_signed_values_odd_padding_and_empty(self):
        self.assertEqual(pack_i4([-8, 7, -1]), bytes([0x78, 0x0f]))
        for size in range(65):
            values=[i%16-8 for i in range(size)]
            self.assertEqual(unpack_i4(pack_i4(values), size), values)
        self.assertEqual(pack_i4([]), b'')
        for values in ([8], [-9], [1.0]):
            with self.assertRaises((ValueError, TypeError)):pack_i4(values)
        for payload, count in ((b'\xf1', 1), (b'', 1), (b'\0', 0), (b'', -1)):
            with self.assertRaises(ValueError):unpack_i4(payload, count)
        values=[-2**31, -1, 2**31-1]
        self.assertEqual(decode_tensor(encode_tensor(values, 'i32'), 'i32', 3), values)
        with self.assertRaises(ValueError):encode_tensor([1], 'i8')

    def test_packed_model_row_layout_parameters_and_column_marker(self):
        for cols in (4, 8):
            package=compile_model(model(), cols=cols)
            words=struct.unpack_from('<16I', package.image)
            self.assertEqual(words[15], FORMAT_MAGIC|cols)
            # Four K rows of four lanes, two bytes/row, including zero K padding.
            self.assertEqual(package.image[words[7]:words[7]+8], bytes([8, 0, 7, 0, 15, 0, 0, 0]))
            self.assertEqual(struct.unpack_from('<2I', package.image, words[8]), (123, 0x003e4000))
            self.assertEqual(package.manifest['input_bytes'], 2)
            self.assertEqual(package.manifest['output_bytes'], 1)
            self.assertEqual(words[9], 0x78)
            self.assertEqual(package.manifest['numeric_format'], NUMERIC_FORMAT)

    def test_native_input_and_weights_reject_int8_range(self):
        bad=model();bad['input']['dtype']='i8'
        with self.assertRaisesRegex(ValueError, 'dtype'):compile_model(bad)
        bad=model();bad['nodes'][0]['weights'][0]=-9
        with self.assertRaisesRegex(ValueError, 'signed i4'):compile_model(bad)
        for multiplier, shift in ((32768, 0), (-1, 0), (1, -32), (1, 32)):
            bad=model();bad['nodes'][1].update(multiplier=[multiplier], shift=[shift])
            with self.assertRaises(ValueError):compile_model(bad)

    def test_package_load_rejects_old_or_mislabeled_encoding(self):
        original=compile_model(model())
        with tempfile.TemporaryDirectory() as directory:
            original.save(directory)
            self.assertEqual(Package.load(directory).image, original.image)
            for key, value in (('abi', 0x20001), ('numeric_format', 0), ('packing', 'byte')):
                package=copy.deepcopy(original);package.manifest[key]=value;package.save(directory)
                with self.assertRaises(ValueError):Package.load(directory)
            original.save(directory)
            (Path(directory)/'model.bin').write_bytes(original.image[:-1])
            with self.assertRaises(ValueError):Package.load(directory)

    def test_host_rejects_mismatch_before_invalidating_current_model(self):
        package=compile_model(model())
        for transport in (Transport(cols=4), Transport(numeric=0)):
            host=Npu(transport)
            with self.assertRaises(ValueError):host.load_model(package)
            self.assertTrue(all(op==Op.READ for op, _, _ in transport.rows))
        transport=Transport();host=Npu(transport);host.load_model(package)
        transport.rows.clear()
        with self.assertRaisesRegex(ValueError, 'padding'):host.infer(bytes([0, 0xf1]))
        self.assertTrue(all(op==Op.READ for op, _, _ in transport.rows))
        with patch.object(host, 'infer', return_value=bytes([0x0d])) as infer:
            self.assertEqual(host.infer_values([-8, 7, -1]), [-3])
            self.assertEqual(infer.call_args.args[0], bytes([0x78, 0x0f]))
        with self.assertRaisesRegex(ValueError, 'count'):host.infer_values([1])

    def test_explicit_lossy_ptq_tracks_bias_units_and_scale(self):
        source=model();source['input']['dtype']='i8'
        source['nodes'][0].update(weights=[-128, 112, -16], bias=[256])
        source['nodes'][1].update(multiplier=[2**30], shift=[-3])
        converted=quantize_int8_model(source)
        self.assertEqual(source['input']['dtype'], 'i8')
        self.assertEqual(converted['nodes'][0]['weights'], [-8, 7, -1])
        self.assertEqual(converted['nodes'][0]['bias'], [1])
        self.assertEqual(converted['nodes'][1]['multiplier'], [0x4000])
        self.assertEqual(converted['nodes'][1]['shift'], [1])
        self.assertFalse(converted['metadata']['int4_conversion']['accuracy_validated'])
        compile_model(converted)

    def test_q15_rounding_and_saturation_examples(self):
        self.assertEqual(requant(-3, 16384, 0), -1)
        self.assertEqual(requant(3, 16384, 0), 2)
        self.assertEqual(requant(-2**31, 32767, 31), -8)
        self.assertEqual(requant(2**31-1, 32767, 31), 7)
        self.assertEqual(requant(-2**31, 0, 0), 0)


if __name__=='__main__':unittest.main()
