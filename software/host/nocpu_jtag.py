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
        reply=self.rpc.execute(f'drscan {self.tap} 72 {packet:018x} -endstate IDLE')
        try:
            if len(reply)>18:
                raise ValueError
            return int(reply,16)
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
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['status','reset','infer'])
    p.add_argument('--package',type=Path);p.add_argument('--input',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=6666)
    args=p.parse_args();rpc=TclRpc(args.host,args.port)
    try:
        npu=Npu(JtagTransport(rpc))
        if args.command=='status':print(json.dumps(npu.status(),indent=2))
        elif args.command=='reset':npu.command(Op.RESET)
        else:
            if not args.package or not args.input or not args.output:p.error('infer requires --package --input --output')
            npu.load_model(Package.load(args.package));args.output.write_bytes(npu.infer(args.input.read_bytes()))
    finally:rpc.close()
if __name__=='__main__':main()
