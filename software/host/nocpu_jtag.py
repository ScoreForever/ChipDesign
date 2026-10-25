#!/usr/bin/env python3
"""ABI 2 host: reusable model loading and arbitrary input/output tensors over JTAG."""
from __future__ import annotations
import argparse
from enum import IntEnum
import json
from pathlib import Path
import socket
import time


class Op(IntEnum):
    READ=1; WRITE=2; MODEL_BEGIN=3; MODEL_COMMIT=4; INPUT_BEGIN=5
    INPUT_COMMIT=6; START=7; ACK=8; RESET=9; CLEAR_ERROR=10

ERRORS={1:'opcode',2:'address',3:'alignment',4:'busy',5:'state',6:'order',
        7:'incomplete',8:'parameter',9:'result pending',10:'timeout',
        17:'program header',18:'descriptor',19:'shape',20:'range',21:'uninitialized source',22:'quantization'}

PERF_NAMES=(
    'active_cycles','model_requests','model_stall_cycles','model_max_stall',
    'model_responses','model_wait_response_cycles',
    'activation_requests','activation_stall_cycles','activation_max_stall',
    'activation_responses','activation_wait_response_cycles','writes','mac_issues',
    'mac_wait_activation_cycles','mac_wait_weight_cycles','mac_wait_slot_cycles',
    'kernel_prepare_cycles','result_slots_peak','weight_banks_peak','inflight_peak',
    'post_lanes','gather_contiguous_groups','gather_scalar_groups','gather_zero_groups')


class TclRpc:
    def __init__(self, host='127.0.0.1', port=6666, timeout=5):
        self.socket=socket.create_connection((host,port),timeout)
        self.socket.settimeout(timeout)
        self.buffer=b''

    def execute(self, command):
        self.socket.sendall(command.encode('ascii')+b'\x1a')
        while b'\x1a' not in self.buffer:
            block=self.socket.recv(4096)
            if not block:
                raise ConnectionError('OpenOCD closed Tcl RPC connection')
            self.buffer+=block
            if len(self.buffer)>1_000_000:
                raise ConnectionError('oversized Tcl RPC response')
        result,self.buffer=self.buffer.split(b'\x1a',1)
        return result.decode('utf-8').strip()

    def close(self):
        self.socket.close()


class JtagTransport:
    def __init__(self, rpc, tap='npu.tap', timeout=5):
        if not tap or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-' for c in tap):
            raise ValueError('invalid TAP name')
        self.rpc,self.tap,self.timeout=rpc,tap,timeout
        self.rpc.execute(f'irscan {tap} 0x12 -endstate IDLE')

    def scan(self, packet):
        # OpenOCD defaults unprefixed numbers to decimal. Use explicit hexadecimal
        # data/address/opcode fields and reassemble its three returned fields.
        reply=self.rpc.execute(
            f'drscan {self.tap} 32 0x{packet & 0xffffffff:08x} '
            f'32 0x{(packet >> 32) & 0xffffffff:08x} '
            f'8 0x{(packet >> 64) & 0xff:02x} -endstate IDLE')
        try:
            words=reply.split()
            if len(words)!=3:
                raise ValueError
            fields=[int(word,16) for word in words]
            if any(len(word)>width//4 or not 0<=value<2**width
                   for word,value,width in zip(words,fields,(32,32,8))):
                raise ValueError
            return fields[0] | (fields[1]<<32) | (fields[2]<<64)
        except ValueError as exc:
            raise RuntimeError(f'OpenOCD drscan failed: {reply}') from exc

    def exchange(self, op, addr=0, data=0):
        if not 0<int(op)<256 or not 0<=addr<2**32 or not -(2**31)<=data<2**32:
            raise ValueError('command field outside packet range')
        # Poll before submission: never overwrite somebody else's pending command.
        previous=self.scan(0)
        if (previous>>70)&1:
            raise RuntimeError('JTAG mailbox is occupied; poll/recover the previous operation first')
        self.scan((int(op)<<64)|(addr<<32)|(int(data)&0xffffffff))
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            response=self.scan(0)
            status=response>>64
            if status&0x20:
                raise RuntimeError('JTAG mailbox overrun; the original operation may have executed')
            if status&0x80 and not status&0x40:
                if (response>>32)&0xffffffff != addr:
                    raise RuntimeError('JTAG response address mismatch')
                code=status&31
                if code:
                    raise RuntimeError(f'NPU error {code}: {ERRORS.get(code,"unknown")} (op={int(op)}, address=0x{addr:x})')
                return response&0xffffffff
        # In particular, do not automatically retry START/COMMIT after timeout.
        raise TimeoutError('JTAG response timed out; operation may have executed; inspect status before retrying')


def signed32(value):
    return value if value<2**31 else value-2**32


class Npu:
    def __init__(self,transport):
        self.transport=transport
        if self.read(0)!=0x20000:raise RuntimeError('requires NPU ABI 2.0')
    def command(self,op,addr=0,data=0):return self.transport.exchange(op,addr,data)
    def read(self,addr):return self.command(Op.READ,addr)
    def write(self,addr,data):return self.command(Op.WRITE,addr,int(data)&0xffffffff)
    def status(self):
        s=self.read(4)
        return dict(busy=bool(s&1),model_ready=bool(s&2),input_ready=bool(s&4),done=bool(s&8),
                    faulted=bool(s&64),error=self.read(8),error_op=self.read(0x34),
                    cycles=self.read(0x38),issues=self.read(0x3c),writes=self.read(0x40))
    def performance(self):
        """Read a completed/idle job's passive counters; never reset or ACK it."""
        if self.read(0x68)!=0x00010018:
            raise RuntimeError('requires performance extension v1 with 24 counters')
        job=self.read(0x2c)
        status=self.read(4)
        if status&1:
            raise RuntimeError('performance counters require an idle/completed NPU')
        values={name:self.read(0x100+4*i) for i,name in enumerate(PERF_NAMES)}
        if self.read(0x2c)!=job or self.read(4)!=status:
            raise RuntimeError('job changed during performance read; discard counters')
        return dict(job=job,**values)
    def configure_trace(self, pc=0, offset=0, trigger_slot=False, enabled=True):
        """Arm the next job; caller must ACK any completed result first."""
        if not 0<=pc<16 or not 0<=offset<2**32:
            raise ValueError('trace PC must be 0..15 and offset must fit uint32')
        self.write(0x188,(pc<<4)|(int(bool(trigger_slot))<<1)|int(bool(enabled)))
        self.write(0x18c,offset)
    def trace(self, include_samples=True):
        from npu_trace import decode_sample
        status=self.read(4);job=self.read(0x2c)
        if status&1:raise RuntimeError('trace requires an idle/completed NPU')
        if self.read(0x180)!=0x00010080:raise RuntimeError('requires trace extension v1, 128-bit samples')
        header={name:self.read(addr) for name,addr in (
            ('depth',0x184),('configured_control',0x188),('configured_offset',0x18c),
            ('flags',0x190),('count',0x194),('first_cycle',0x198),('last_cycle',0x19c),
            ('sample_stride',0x1a0),('operator_stride',0x1a4),('max_ops',0x1a8),
            ('captured_control',0x1ac),('captured_offset',0x1b0))}
        if header['depth'] not in (0,1024) or header['count']>header['depth'] or header['sample_stride']!=16 or header['operator_stride']!=32 or header['max_ops']!=16:
            raise RuntimeError('unsupported/corrupt trace geometry')
        if include_samples and header['depth']==0:
            raise RuntimeError('waveform trace is not enabled in this hardware build; use trace(include_samples=False) for operator summaries')
        op_count=self.read(0x14)
        if op_count>16:raise RuntimeError('corrupt trace operator count')
        names=('start_cycle','end_cycle','descriptor','flags','wait_activation_cycles','wait_weight_cycles','wait_slot_cycles','mac_issues')
        operators=[]
        for pc in range(op_count):
            row={name:self.read(0x200+pc*32+i*4) for i,name in enumerate(names)}
            row.update(pc=pc,opcode=row['descriptor']&255)
            row['duration_cycles']=((row['end_cycle']-row['start_cycle'])&0xffffffff) if row['flags']&6 else None
            operators.append(row)
        samples=[]
        if include_samples:
            for index in range(header['count']):
                words=[self.read(0x1000+index*16+i*4) for i in range(4)]
                samples.append(dict(words=words,**decode_sample(words)))
            if samples and (samples[0]['cycle']!=header['first_cycle'] or samples[-1]['cycle']!=header['last_cycle']):
                raise RuntimeError('trace timestamps do not match header')
            if any(((b['cycle']-a['cycle'])&0xffffffff)!=1 for a,b in zip(samples,samples[1:])):
                raise RuntimeError('trace samples are not consecutive')
            if any(x['pc']!=((header['captured_control']>>4)&15) for x in samples):
                raise RuntimeError('trace PC does not match capture configuration')
        if self.read(0x2c)!=job or self.read(4)!=status:
            raise RuntimeError('job changed during trace read; discard capture')
        return dict(version=1,job=job,status=status,header=header,operators=operators,samples=samples)
    def load_model(self,package):
        m=package.manifest
        # All host checks happen before MODEL_BEGIN invalidates the old model.
        caps={r:self.read(r) for r in (0x50,0x54,0x58,0x5c,0x60)}
        if m['abi']!=0x20000 or m['cols']!=(caps[0x58]&65535):raise ValueError('ABI/array mismatch; recompile package')
        if len(package.image)!=m['model_bytes'] or len(package.image)%4 or len(package.image)>caps[0x54]:raise ValueError('model capacity/length')
        if not 0<m['op_count']<=caps[0x60] or m['activation_peak']>caps[0x50]:raise ValueError('program/activation capacity')
        self.command(Op.MODEL_BEGIN)
        for reg,key in zip(range(0x10,0x28,4),('model_bytes','op_count','input_base','input_bytes','output_base','output_bytes')):
            self.write(reg,m[key])
        self.write(0x64,int(m['input_dtype']=='i32'))
        for offset in range(0,len(package.image),4):self.write(0x10000+offset,int.from_bytes(package.image[offset:offset+4],'little'))
        self.command(Op.MODEL_COMMIT)
    def infer(self,frame,timeout=10):
        frame=bytes(frame);base=self.read(0x18);length=self.read(0x1c)
        if len(frame)!=length:raise ValueError(f'expected {length} input bytes')
        self.command(Op.INPUT_BEGIN)
        for offset in range(0,len(frame),4):self.write(0x20000+base+offset,int.from_bytes(frame[offset:offset+4].ljust(4,b'\0'),'little'))
        self.command(Op.INPUT_COMMIT);self.command(Op.START)
        deadline=time.monotonic()+timeout
        while True:
            s=self.read(4)
            if s&64:raise RuntimeError(f'NPU fault {self.read(8)}, operator {self.read(0x34)}')
            if s&8:break
            if time.monotonic()>deadline:raise TimeoutError('inference still active; inspect status, do not automatically retry START')
            time.sleep(0.001)
        base=self.read(0x20);length=self.read(0x24)
        result=b''.join(self.read(0x20000+base+i).to_bytes(4,'little') for i in range(0,length,4))[:length]
        self.command(Op.ACK)
        return result

def main():
    from npu_program import Package
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['status','perf','trace','trace-config','reset','infer'])
    p.add_argument('--package',type=Path);p.add_argument('--input',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=6666)
    p.add_argument('--pc',type=int,default=0);p.add_argument('--offset',type=int,default=0)
    p.add_argument('--trigger-slot',action='store_true');p.add_argument('--disable',action='store_true')
    p.add_argument('--period-ns',type=int,default=20)
    args=p.parse_args();rpc=TclRpc(args.host,args.port)
    try:
        npu=Npu(JtagTransport(rpc))
        if args.command=='status':print(json.dumps(npu.status(),indent=2))
        elif args.command=='perf':print(json.dumps(npu.performance(),indent=2))
        elif args.command=='trace-config':npu.configure_trace(args.pc,args.offset,args.trigger_slot,not args.disable)
        elif args.command=='trace':
            from npu_trace import export_trace
            if not args.output:p.error('trace requires --output path.vcd')
            capture=npu.trace()
            if args.package:
                import struct
                package=Package.load(args.package)
                if package.manifest['op_count']!=len(capture['operators']):raise ValueError('package/trace operator count mismatch')
                for row in capture['operators']:
                    if row['flags']&8 and row['descriptor']!=struct.unpack_from('<I',package.image,row['pc']*64)[0]:
                        raise ValueError('package/trace descriptor mismatch')
                    if row['pc']<len(package.manifest['groups']):row['logical_ops']=package.manifest['groups'][row['pc']]
            print(json.dumps(export_trace(capture,args.output,args.period_ns),indent=2))
        elif args.command=='reset':npu.command(Op.RESET)
        else:
            if not args.package or not args.input or not args.output:p.error('infer requires --package --input --output')
            npu.load_model(Package.load(args.package));args.output.write_bytes(npu.infer(args.input.read_bytes()))
    finally:rpc.close()
if __name__=='__main__':main()
