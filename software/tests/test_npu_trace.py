import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from nocpu_jtag import Npu, Op
from npu_trace import decode_sample, export_trace


class TraceRegisters:
    def __init__(self, busy=False, changed=False):
        self.reads=[];self.writes=[];self.changed=changed
        self.registers={0:0x20000,4:3 if busy else 10,0x2c:7,0x14:1,
            0x180:0x10080,0x184:1024,0x188:0x31,0x18c:99,0x190:5,0x194:2,
            0x198:2,0x19c:3,0x1a0:16,0x1a4:32,0x1a8:16,0x1ac:1,0x1b0:2}
        for i,value in enumerate((0,4,0x301,11,0,0,2,2)):self.registers[0x200+i*4]=value
        for i in range(2):
            for word,value in enumerate((i+2,0x400,0x04560100,0x10810123)):
                self.registers[0x1000+i*16+word*4]=value

    def exchange(self,op,addr=0,data=0):
        if op==Op.WRITE:self.writes.append((addr,data));return 0
        assert op==Op.READ,'Trace must not ACK or reset'
        self.reads.append(addr)
        if self.changed and addr==0x2c and self.reads.count(addr)>1:return 8
        return self.registers[addr]


class TraceTests(unittest.TestCase):
    def test_decode_fields_and_bit_order(self):
        row=decode_sample([42,0xFFFFFFFF,0x04560100,0xBA810123])
        self.assertEqual((row['cycle'],row['model_address'],row['activation_address']),(42,0x100,0x456))
        self.assertEqual((row['pc'],row['opcode'],row['quantized'],row['state']),(10,3,1,8))
        self.assertEqual((row['write_address'],row['write_mask']),(0x123,1))
        self.assertEqual((row['slot_busy'],row['slot_ready'],row['weight_banks'],row['inflight']),(15,15,3,63))
        self.assertEqual((row['wait_slot'],row['gather_state'],row['load_state']),(1,3,3))

    def test_read_capture_preserves_snapshotted_configuration(self):
        registers=TraceRegisters();capture=Npu(registers).trace()
        self.assertEqual(capture['header']['captured_control'],1)
        self.assertEqual(capture['header']['configured_control'],0x31)
        self.assertEqual(capture['operators'][0]['duration_cycles'],4)
        self.assertEqual([x['cycle'] for x in capture['samples']],[2,3])
        self.assertFalse(registers.writes)

    def test_metadata_only_does_not_read_ram(self):
        registers=TraceRegisters();capture=Npu(registers).trace(include_samples=False)
        self.assertFalse(capture['samples'])
        self.assertFalse(any(x>=0x1000 for x in registers.reads))

    def test_busy_race_and_geometry_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError,'idle/completed'):Npu(TraceRegisters(busy=True)).trace()
        with self.assertRaisesRegex(RuntimeError,'job changed'):Npu(TraceRegisters(changed=True)).trace()
        registers=TraceRegisters();registers.registers[0x194]=1025
        with self.assertRaisesRegex(RuntimeError,'geometry'):Npu(registers).trace()

    def test_configure_requires_bounded_pc_and_offset(self):
        registers=TraceRegisters();npu=Npu(registers);npu.configure_trace(3,5,True)
        self.assertEqual(registers.writes,[(0x188,0x33),(0x18c,5)])
        for pc,offset in ((16,0),(-1,0),(0,-1),(0,2**32)):
            with self.assertRaises(ValueError):npu.configure_trace(pc,offset)

    def test_export_real_rows_csv_json_and_clock(self):
        capture=Npu(TraceRegisters()).trace()
        with tempfile.TemporaryDirectory() as directory:
            result=export_trace(capture,Path(directory)/'wave.vcd')
            text=Path(result['vcd']).read_text()
            self.assertIn('$timescale 1ns $end',text)
            self.assertIn('#40\n1s0',text);self.assertIn('#50\n0s0',text)
            self.assertIn('#60\n1s0',text);self.assertIn('#80\n$comment end of captured window',text)
            with Path(result['csv']).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
            self.assertEqual([int(x['cycle']) for x in rows],[2,3])
            self.assertEqual(json.loads(Path(result['json']).read_text())['period_ns'],20)
            with self.assertRaises(ValueError):export_trace(capture,Path(directory)/'odd.vcd',19)

    def test_invalid_samples_and_nonconsecutive_times(self):
        for words in ([0]*3,[0,0,0,-1],[2**32,0,0,0]):
            with self.assertRaises(ValueError):decode_sample(words)
        capture=Npu(TraceRegisters()).trace();capture['samples'][1]['words'][0]=9
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'consecutive'):export_trace(capture,Path(directory)/'wave.vcd')


if __name__=='__main__':unittest.main()
