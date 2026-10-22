#!/usr/bin/env python3
"""CPU-less NPU host using OpenOCD Tcl RPC (jtag-only; no RISC-V target).

Start OpenOCD with your adapter config and software/host/nocpu_openocd.cfg.
Then: python software/host/nocpu_jtag.py status
      python software/host/nocpu_jtag.py infer --package model.npz --input frame.bin
Input is 320 signed INT8 feature bytes, not PCM audio. NumPy is needed only for
NPZ deployment packages. The standard-library transport also supports RTL hex
vectors with `infer-vectors --directory ...`.
"""
from __future__ import annotations
import argparse
from enum import IntEnum
import json
from pathlib import Path
import socket
import time


class Op(IntEnum):
    READ=1; WRITE=2; MODEL_BEGIN=3; PARAM_COMMIT=4; MODEL_COMMIT=5
    INPUT_BEGIN=6; INPUT_COMMIT=7; START=8; ACK=9; CLEAR_ERROR=10; RESET=11
    MODULE_WRITE=32; MODULE_READ=33


ERRORS = {1:'illegal opcode',2:'invalid address',3:'unaligned address',4:'busy',
          5:'invalid lifecycle state',6:'out-of-order transfer',7:'incomplete transfer',
          8:'invalid parameter',9:'unacknowledged result',10:'hardware timeout',
          11:'unsupported extension',12:'backend not ready'}


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
        # OpenOCD documents drscan as a list of fields and warns against fields
        # wider than 32 bits for portability.  A single 72-bit value works on
        # some builds but silently returns zeros on others.  The register shifts
        # least-significant bits first, so send data, address, then opcode.
        packet &= (1 << 72) - 1
        data=packet & 0xffffffff
        addr=(packet >> 32) & 0xffffffff
        opcode=(packet >> 64) & 0xff
        reply=self.rpc.execute(
            f'drscan {self.tap} 32 0x{data:08x} 32 0x{addr:08x} '
            f'8 0x{opcode:02x} -endstate IDLE'
        )
        try:
            fields=reply.split()
            if len(fields)!=3 or len(fields[0])>8 or len(fields[1])>8 or len(fields[2])>2:
                raise ValueError
            return int(fields[0],16) | (int(fields[1],16)<<32) | (int(fields[2],16)<<64)
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
    def __init__(self, transport):
        self.transport=transport
        version=self.read(0)
        if version!=0x10000:
            raise RuntimeError(f'unsupported controller ABI 0x{version:08x}')

    def command(self, op, addr=0, data=0):
        return self.transport.exchange(op,addr,data)

    def read(self, addr):
        return self.command(Op.READ,addr)

    def write(self, addr, data):
        return self.command(Op.WRITE,addr,int(data))

    def status(self):
        raw=self.read(4)
        return {'status':raw, 'busy':bool(raw&1),'model_ready':bool(raw&2),
                'input_ready':bool(raw&4),'done':bool(raw&8),'faulted':bool(raw&128),
                'error':self.read(8),'job_id':self.read(0x18),'completed_id':self.read(0x1c),
                'core_cycles':self.read(0x20),'task_cycles':self.read(0x24)}

    def load_model(self, weights, params, classes=4):
        # Validate the complete host package before invalidating the device model.
        weights=list(weights)
        if len(weights)<241 or any(not 0<=int(w)<2**64 for w in weights):
            raise ValueError('need at least 241 packed 64-bit weight words')
        if classes not in range(1,9) or set(params)!={0,1,3}:
            raise ValueError('invalid classes or parameter groups')
        for layer,(bias,mult,shift) in params.items():
            if len(bias)!=8 or any(not -(2**31)<=int(v)<2**32 for v in bias):
                raise ValueError('each layer needs eight INT32 bias lanes (pad unused FC lanes)')
            if layer!=3 and (len(mult)!=8 or len(shift)!=8 or
                            any(not 0<=int(v)<2**31 for v in mult) or
                            any(not -31<=int(v)<=31 for v in shift)):
                raise ValueError('invalid convolution multiplier/shift')
        self.command(Op.MODEL_BEGIN)
        self.write(0x10,classes)
        for index,word in enumerate(weights[:241]):
            self.write(0x2000+index*8,int(word)&0xffffffff)
            self.write(0x2004+index*8,int(word)>>32)
        for layer in (0,1,3):
            bias,mult,shift=params[layer]
            for lane in range(8):
                base=0x4000+layer*0x100+lane*4
                self.write(base,bias[lane])
                if layer!=3:
                    self.write(base+0x20,mult[lane]);self.write(base+0x40,shift[lane])
            self.command(Op.PARAM_COMMIT,data=layer)
        self.command(Op.MODEL_COMMIT)

    def infer(self, frame, timeout=5):
        frame=bytes(frame)
        if len(frame)!=320:
            raise ValueError('expected exactly 320 feature bytes')
        self.command(Op.INPUT_BEGIN)
        for index in range(80):
            self.write(0x1000+index*4,int.from_bytes(frame[index*4:index*4+4],'little'))
        self.command(Op.INPUT_COMMIT)
        job=self.command(Op.START)
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=self.read(4)
            if state&128:
                raise RuntimeError(f'NPU fault: {self.read(8)}; soft reset and reload required')
            if state&8:
                classes=self.read(0x10)
                logits=[signed32(self.read(0x100+4*i)) for i in range(classes)]
                result={'job_id':job,'logits':logits,'core_cycles':self.read(0x20),
                        'task_cycles':self.read(0x24)}
                self.command(Op.ACK)
                return result
            time.sleep(0.001)
        raise TimeoutError('inference wait timed out; inspect device before retrying')


def hex_values(path):
    return [int(line.strip(),16) for line in Path(path).read_text().splitlines() if line.strip()]


def fixture(directory, sample=0, classes=4):
    directory=Path(directory)
    weights=hex_values(directory/'weights.hex')
    if (directory/'bias.hex').is_file():
        bias=hex_values(directory/'bias.hex');mult=hex_values(directory/'multiplier.hex')
        shift=[x if x<128 else x-256 for x in hex_values(directory/'shift.hex')]
        params={layer:(bias[layer*8:layer*8+8],mult[layer*8:layer*8+8],shift[layer*8:layer*8+8]) for layer in (0,1,3)}
        inputs=hex_values(directory/'input.hex')
    else:
        params={layer:(hex_values(directory/f'conv{layer+1}_bias.hex'),
                       hex_values(directory/f'conv{layer+1}_mult.hex'),
                       [x if x<128 else x-256 for x in hex_values(directory/f'conv{layer+1}_shift.hex')]) for layer in (0,1)}
        params[3]=(hex_values(directory/'fc_bias.hex'),[],[])
        inputs=hex_values(directory/'inputs.hex')
    frame=bytes(inputs[sample*320:(sample+1)*320])
    if len(frame)!=320:
        raise ValueError('sample index outside input vectors')
    return weights,params,classes,frame


def package(path):
    import numpy as np
    with np.load(path,allow_pickle=False) as p:
        if p['model_head'].item()!='flatten' or tuple(p['weight_mem'].shape)!=(256,8):
            raise ValueError('expected production Flatten deployment package')
        classes=int(p['num_classes'])
        if classes!=4:
            raise ValueError('production deployment package must contain four classes')
        weights=[sum((int(v)&255)<<(8*i) for i,v in enumerate(row)) for row in p['weight_mem']]
        params={layer:(p[f'conv{layer+1}_bias'].tolist(),p[f'conv{layer+1}_mult'].tolist(),
                       p[f'conv{layer+1}_shift'].tolist()) for layer in (0,1)}
        params[3]=(p['fc_bias'].tolist()+[0]*(8-classes),[],[])
        compare=(p['fc_cmp_mult'].tolist(),p['fc_cmp_shift'].tolist())
    return weights,params,classes,compare


def main():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host',default='127.0.0.1');ap.add_argument('--port',type=int,default=6666)
    ap.add_argument('--tap',default='npu.tap')
    sub=ap.add_subparsers(dest='action',required=True)
    sub.add_parser('status');sub.add_parser('reset');sub.add_parser('ack');sub.add_parser('clear-error')
    read=sub.add_parser('read');read.add_argument('address',type=lambda x:int(x,0));read.add_argument('--count',type=int,default=1)
    infer=sub.add_parser('infer');infer.add_argument('--package',required=True);infer.add_argument('--input',required=True)
    reuse=sub.add_parser('infer-reuse');reuse.add_argument('--input',required=True)
    vectors=sub.add_parser('infer-vectors');vectors.add_argument('--directory',required=True)
    vectors.add_argument('--sample',type=int,default=0);vectors.add_argument('--classes',type=int,default=4)
    args=ap.parse_args()
    rpc=TclRpc(args.host,args.port)
    try:
        npu=Npu(JtagTransport(rpc,args.tap))
        if args.action=='status':result=npu.status()
        elif args.action in ('reset','ack','clear-error'):
            npu.command({'reset':Op.RESET,'ack':Op.ACK,'clear-error':Op.CLEAR_ERROR}[args.action]);result=npu.status()
        elif args.action=='read':
            if args.count<1 or args.count>1024:raise ValueError('count must be 1..1024')
            result={f'0x{args.address+4*i:08x}':f'0x{npu.read(args.address+4*i):08x}' for i in range(args.count)}
        elif args.action=='infer-vectors':
            weights,params,classes,frame=fixture(args.directory,args.sample,args.classes)
            npu.load_model(weights,params,classes);result=npu.infer(frame)
        elif args.action=='infer-reuse':result=npu.infer(Path(args.input).read_bytes())
        else:
            weights,params,classes,compare=package(args.package)
            frame=Path(args.input).read_bytes()
            if len(frame)!=320:raise ValueError('expected exactly 320 feature bytes')
            npu.load_model(weights,params,classes);result=npu.infer(frame)
            # Preserve the existing Q31 classifier contract, including rounding.
            import sys
            sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
            from quantize import multiply_by_quantized_multiplier
            scores=[multiply_by_quantized_multiplier(v,m,s) for v,m,s in zip(result['logits'],*compare)]
            result['class_id']=max(range(classes),key=lambda i:scores[i])
            result['label']=['yes','no','up','down'][result['class_id']]
        print(json.dumps(result,indent=2))
    finally:
        rpc.close()


if __name__=='__main__':
    main()
