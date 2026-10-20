#!/usr/bin/env python3
"""Reproducible main+H16 FPGA regression. Run in WSL; never programs implicitly.

prepare: freeze independent golden vectors and build CPU firmware.
board: run frozen cases against an explicitly identified live bitstream.
Requires the external CV32E40P OpenOCD session on localhost:6666/3333.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'software'),str(ROOT/'software/tools')]
import numpy as np
import golden_model
spec=importlib.util.spec_from_file_location('independent',ROOT/'hardware/npu/tools/tinycnn8_golden.py')
independent=importlib.util.module_from_spec(spec); spec.loader.exec_module(independent)

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value): Path(path).write_text(json.dumps(value,indent=2)+'\n')
def checked(argv,out=None):
    r=subprocess.run(list(map(str,argv)),cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=600)
    if out: Path(out).write_text(r.stdout)
    if r.returncode: raise RuntimeError(r.stdout[-5000:])
    return r.stdout
def bounded_output(path):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT/'build') or path==ROOT/'build': raise ValueError('Output must be inside build/')
    return path
def result_directory(out,name):
    if not name or Path(name).name!=name or name in ('.','..'): raise ValueError('Run name must be one directory name')
    dest=out/name;dest.mkdir(exist_ok=False)
    return dest
def validate_manifest(out,manifest):
    if digest(out/'validation.bin')!=manifest['firmware_sha256']: raise ValueError('Firmware hash mismatch')
    for name,h in manifest['source_sha256'].items():
        if digest(ROOT/name)!=h: raise ValueError('Source changed after prepare: '+name)
    for c in manifest['cases']:
        if digest(out/c['payload'])!=c['sha256'] or digest(out/c['model'])!=c['model_sha256']:
            raise ValueError('Fixture hash mismatch: '+c['name'])
def from_package(pkg,values):
    classes=int(pkg['num_classes'])
    return dict(classes=classes,input=list(map(int,values)),
        conv1=dict(weights=np.asarray(pkg['conv1_w']).reshape(3,3,1,8).transpose(3,0,1,2).tolist(),
                   bias=pkg['conv1_bias'].tolist(),multiplier=pkg['conv1_mult'].tolist(),shift=pkg['conv1_shift'].tolist()),
        conv2=dict(weights=np.asarray(pkg['conv2_w']).reshape(3,3,8,8).transpose(3,0,1,2).tolist(),
                   bias=pkg['conv2_bias'].tolist(),multiplier=pkg['conv2_mult'].tolist(),shift=pkg['conv2_shift'].tolist()),
        fc=dict(weights=pkg['fc_w'][:,:classes].T.tolist(),bias=pkg['fc_bias'].tolist()))
def payload(model,mode,repeats,seed):
    bias,mult,shift=independent.parameter_arrays(model)
    words=[model['classes'],mode,repeats,seed]
    for word in independent.pack_weights(model)[:241]: words.extend([word&0xffffffff,word>>32])
    words+=bias[:16]+bias[24:32]+mult[:16]+shift[:16]
    data=struct.pack('<'+'I'*len(words),*[int(x)&0xffffffff for x in words])
    data+=bytes(int(x)&255 for x in model['input'])
    assert len(data)==2488
    return data
def prepare(args):
    out=bounded_output(args.output);out.mkdir(parents=True,exist_ok=False)
    cases=[]
    def case(name,model,mode,repeats=2,source='synthetic'):
        expected=independent.run_model(model)
        index=len(cases); stem=f'{index:03}_{name}'
        file=out/(stem+'.bin'); file.write_bytes(payload(model,mode,repeats,index+101))
        model_file=out/(stem+'.json');save(model_file,model)
        cases.append(dict(name=name,payload=file.name,sha256=digest(file),model=model_file.name,
            model_sha256=digest(model_file),mode=mode,repeats=repeats,classes=model['classes'],source=source,
            expected=expected['fc']+[0]*(8-model['classes']),
            pool1_sha256=hashlib.sha256(bytes(x&255 for x in expected['pool1'])).hexdigest(),
            pool2_sha256=hashlib.sha256(bytes(x&255 for x in expected['pool2'])).hexdigest(),
            pool1_saturated=sum(x==127 for x in expected['pool1']),pool2_saturated=sum(x==127 for x in expected['pool2'])))
    for classes in range(1,9):
        for mode in range(3):
            case(f'random_c{classes}_mode{mode}',independent.build_model(100+classes,classes,'random'),mode)
    for pattern in ('impulse','extreme'):
        for classes in (1,4,8): case(f'{pattern}_c{classes}',independent.build_model(200+classes,classes,pattern),1)
    for shift in (-31,0,31):
        m=independent.build_model(305,8,'extreme')
        for name in ('conv1','conv2'):
            m[name]['shift']=[shift]*8
            m[name]['multiplier']=[0,1,0x40000000,0x7fffffff]*2
            m[name]['bias']=[-2147483648,2147483647,-1,0,1,127,-128,0x40000000]
        m['fc']['bias']=[-2147483648,2147483647,-1,0,1,127,-128,0x40000000]
        case(f'quant_shift{shift}',m,2)
    case('mmio_negative_busy',independent.build_model(402,8,'random'),3,8)
    case('soak_model_reuse',independent.build_model(403,4,'random'),1,args.soak)
    package=ROOT/'software/models/tinycnn8_flatten_strong.npz'
    with np.load(package,allow_pickle=False) as p: pkg={k:p[k] for k in p.files}
    rng=random.Random(500)
    inputs=[[rng.randint(-128,127) for _ in range(320)] for _ in range(4)]
    inputs+=[[0]*320,[-128]*320,[127]*320,[-128,127]*160]
    for i,values in enumerate(inputs):
        m=from_package(pkg,values)
        # Cross-check two independently implemented software references.
        fast=golden_model.infer(pkg,np.array(values,dtype=np.int8)).tolist()
        if independent.run_model(m)['fc']!=fast: raise ValueError('Software oracle disagreement')
        case(f'trained_weights_features{i}',m,i%3,source='trained_weights_synthetic_features')
    prefix=args.tool_prefix;fw=ROOT/'verification/fpga/firmware'
    checked([prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-Os','-g','-ffreestanding','-fno-builtin',
        '-msmall-data-limit=0','-mno-relax','-nostdlib','-static','-Wall','-Wextra','-Werror',
        '-I'+str(ROOT/'software/drivers'),fw/'start.S',fw/'validate.c','-T'+str(fw/'link.ld'),
        '-Wl,-Map='+str(out/'validation.map'),'-o',out/'validation.elf','-lgcc'],out/'compile.log')
    checked([prefix+'objcopy','-O','binary',out/'validation.elf',out/'validation.bin'])
    checked([prefix+'objdump','-d',out/'validation.elf'],out/'validation.dump')
    sources=[Path(__file__),ROOT/'verification/fpga/report.py',ROOT/'verification/fpga/run.ps1',
        ROOT/'verification/fpga/audit_cdc.tcl',ROOT/'verification/fpga/gdb_check.gdb',
        fw/'start.S',fw/'validate.c',fw/'link.ld',ROOT/'software/drivers/npu_mmio.h',
        ROOT/'hardware/npu/tools/tinycnn8_golden.py',ROOT/'software/golden_model.py']
    # Freeze the hardware inputs too: a later source edit invalidates this run.
    sources+=sorted(p for p in (ROOT/'hardware').rglob('*') if p.suffix in ('.sv','.svh','.v','.xdc','.f','.tcl'))
    snapshot=out/'source_snapshot';snapshot.mkdir()
    for source in sources:
        dest=snapshot/source.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(source.read_bytes())
    manifest=dict(schema=2,cases=cases,firmware_sha256=digest(out/'validation.bin'),
        prepared_utc=datetime.now(timezone.utc).isoformat(),
        model_package_sha256=digest(package),toolchain=checked([prefix+'gcc','--version']).splitlines()[0],
        commit=checked(['git','rev-parse','HEAD']).strip(),
        source_sha256={str(p.relative_to(ROOT)):digest(p) for p in sources})
    save(out/'manifest.json',manifest)
    print(f'PREPARED cases={len(cases)} jobs={sum(c["repeats"] for c in cases)} firmware_bytes={(out/"validation.bin").stat().st_size}',flush=True)

class Rpc:
    def __init__(self): self.socket=socket.create_connection(('127.0.0.1',6666),10);self.socket.settimeout(120); self.buffer=b''
    def call(self,command):
        # Wrap Tcl errors instead of accepting diagnostic text as a value.
        wrapped='if {[catch {'+command+'} _v]} {format "ERROR:%s" $_v} else {format "OK:%s" $_v}'
        self.socket.sendall(wrapped.encode()+b'\x1a')
        while b'\x1a' not in self.buffer:
            block=self.socket.recv(65536)
            if not block: raise RuntimeError('OpenOCD disconnected')
            self.buffer+=block
        value,self.buffer=self.buffer.split(b'\x1a',1); value=value.decode()
        if not value.startswith('OK:'): raise RuntimeError(value)
        return value[3:]
    def read(self,addr,count): return [int(x,0) for x in self.call(f'read_memory {addr:#x} 32 {count}').split()]
    def close(self): self.socket.close()

def board(args):
    out=bounded_output(args.output);manifest=json.loads((out/'manifest.json').read_text())
    results=result_directory(out,args.run_name)
    report=dict(schema=1,status='RUNNING',cases=[],gaps=[
        'Finite directed/random regression is not an exhaustive RTL proof.',
        'Board comparison observes logits; intermediate layers are checked in RTL simulation.',
        'Default trained-model frames are synthetic features, not real speech accuracy evidence.',
        'CPU ISA completeness, pin-level electrical margins and arbitrary AXI interleavings are not established.'])
    bit=Path(args.bitstream).resolve()
    rpc=None
    try:
        if digest(bit)!=args.bitstream_sha256.lower(): raise ValueError('Bitstream hash differs from explicit expected identity')
        report.update(bitstream=str(bit),bitstream_sha256=digest(bit),manifest_sha256=digest(out/'manifest.json'),
            started_utc=datetime.now(timezone.utc).isoformat(),identity_scope='Expected bitstream file identity; verify programming receipt separately')
        validate_manifest(out,manifest)
        rpc=Rpc();rpc.call('halt');report['adapter_speed']=rpc.call('adapter speed')
        rpc.call(f'adapter speed {args.khz}')
        report['cpu']=rpc.call('targets')
        rpc.call(f'load_image {{{out/"validation.bin"}}} 0x80000000 bin')
        rpc.call(f'verify_image {{{out/"validation.bin"}}} 0x80000000 bin')
        for index,c in enumerate(manifest['cases']):
            if args.limit and index>=args.limit: break
            p=out/c['payload']
            if digest(p)!=c['sha256'] or digest(out/c['model'])!=c['model_sha256']: raise ValueError('Fixture hash mismatch')
            rpc.call(f'load_image {{{p}}} 0x80001000 bin')
            rpc.call(f'verify_image {{{p}}} 0x80001000 bin')
            rpc.call('mww 0x80001d00 0; resume 0x80000000')
            deadline=time.monotonic()+args.timeout
            while True:
                time.sleep(.05);rpc.call('halt');words=rpc.read(0x80001d00,32)
                if words[0] in (0xc0dec0de,0xdeadbeef): break
                if time.monotonic()>deadline: raise TimeoutError(f'CPU firmware timeout {c["name"]} {words[:8]}')
                rpc.call('resume')
            (results/(c['name']+'.bin')).write_bytes(struct.pack('<32I',*words))
            signed=lambda x:x-(1<<32) if x>>31 else x
            got=list(map(signed,words[8:16]))
            item=dict(name=c['name'],mode=c['mode'],classes=c['classes'],source=c['source'],
                magic=hex(words[0]),failure_line=words[1],checks=words[2],jobs=words[3],
                abi=hex(words[4]),status=words[5],error=words[6],cycles=words[7],logits=got,
                expected=c['expected'],profile=words[16:28],dma_checks=words[28],memory_checks=words[29],busy_checks=words[30],irq_checks=words[31])
            report['cases'].append(item);save(results/'summary.json',report)
            if words[0]!=0xc0dec0de: raise AssertionError(f'{c["name"]}: firmware failure at line {words[1]}')
            if got!=c['expected']: raise AssertionError(f'{c["name"]}: got {got}, expected {c["expected"]}')
            if words[3]!=c['repeats'] or words[6] or words[4]!=0x20002: raise AssertionError('Incomplete jobs/ABI/error')
            if c['mode']==3 and words[30]!=c['repeats']: raise AssertionError('Missing busy-negative coverage')
            if words[31]!=2*(words[3]+words[28]): raise AssertionError('Missing CPU IRQ pending/clear coverage')
            print(f'PASS {index+1}/{len(manifest["cases"])} {c["name"]} jobs={words[3]} checks={words[2]} cycles={words[7]} logits={got}',flush=True)
        report['status']='PASS' if len(report['cases'])==len(manifest['cases']) else 'PARTIAL'
        report['total_jobs']=sum(c['jobs'] for c in report['cases'])
        report['total_checks']=sum(c['checks'] for c in report['cases'])
        report['irq_checks']=sum(c['irq_checks'] for c in report['cases'])
        report['classes']=sorted(set(c['classes'] for c in report['cases']))
    except BaseException as error:
        report.update(status='FAIL',error=str(error));raise
    finally:
        if rpc:
            try: rpc.call('halt')
            finally: rpc.close()
        save(results/'summary.json',report)
    print(f'BOARD_{report["status"]}: cases={len(report["cases"])} jobs={report["total_jobs"]}',flush=True)

def recovery(args):
    out=bounded_output(args.output); manifest=json.loads((out/'manifest.json').read_text())
    dest=result_directory(out,args.run_name)
    report=dict(status='RUNNING',sram=[],resets=[],manifest_sha256=digest(out/'manifest.json'),
        started_utc=datetime.now(timezone.utc).isoformat(),
        scope='reset halt via RISC-V ndmreset; active workload interruption is not cycle-exact busy reset')
    rpc=None
    try:
        validate_manifest(out,manifest)
        rpc=Rpc()
        rpc.call('halt');rpc.call('adapter speed 500')
        # Save and restore all SRAM; do not execute a CPU instruction while patterns overwrite code/stack.
        backup=dest/'sram_backup.bin'
        rpc.call(f'dump_image {{{backup}}} 0x80000000 8192')
        if len(backup.read_bytes())!=8192: raise ValueError('Incomplete SRAM backup')
        rng=random.Random(614)
        patterns={'zero':bytes(8192),'ones':b'\xff'*8192,'alternating':b'\x55\xaa'*4096,
                  'walking_bits':b''.join(struct.pack('<I',1<<(i%32)) for i in range(2048)),
                  'address':b''.join(struct.pack('<I',0x80000000+4*i) for i in range(2048)),
                  'random':bytes(rng.randrange(256) for _ in range(8192))}
        try:
            for name,data in patterns.items():
                image=dest/(name+'.bin');image.write_bytes(data)
                rpc.call(f'load_image {{{image}}} 0x80000000 bin')
                rpc.call(f'verify_image {{{image}}} 0x80000000 bin')
                rb=dest/(name+'_readback.bin');rpc.call(f'dump_image {{{rb}}} 0x80000000 8192')
                if rb.read_bytes()!=data: raise AssertionError('SRAM pattern mismatch '+name)
                report['sram'].append(dict(pattern=name,bytes=8192,sha256=digest(rb),status='PASS'))
                print('PASS whole SRAM '+name,flush=True)
        finally:
            rpc.call(f'load_image {{{backup}}} 0x80000000 bin')
            rpc.call(f'verify_image {{{backup}}} 0x80000000 bin')
        fixture=next(c for c in manifest['cases'] if c['name']=='random_c4_mode1')
        original=(out/fixture['payload']).read_bytes()
        if digest(out/fixture['payload'])!=fixture['sha256']: raise ValueError('Fixture hash mismatch')
        for i in range(args.resets):
            # Arm a long-running valid workload for alternate trials.
            active=bool(i%2)
            rpc.call(f'load_image {{{out/"validation.bin"}}} 0x80000000 bin')
            if active:
                data=bytearray(original);struct.pack_into('<I',data,8,1000000)
                cfg=dest/f'active_{i}.bin';cfg.write_bytes(data)
                rpc.call(f'load_image {{{cfg}}} 0x80001000 bin')
                rpc.call('mww 0x80001d00 0; resume 0x80000000; sleep 50; halt')
                before=rpc.read(0x80001d00,8)
                if before[0]!=0x12345678 or before[3]==0: raise AssertionError('Long workload not active')
            else: before=rpc.read(0x80001d00,8)
            # Standard OpenOCD reset uses RISC-V DM ndmreset, retaining the debug transport.
            reset_log=rpc.call('reset halt')
            registers={hex(a):rpc.read(a,1)[0] for a in (0x70000004,0x70000008,0x7000000c,0x70000410,0x7000006c)}
            if registers['0x70000008']!=4 or registers['0x7000000c'] or registers['0x70000410'] or registers['0x7000006c']:
                raise AssertionError('Reset did not clear NPU/DMA control state '+str(registers))
            rpc.call(f'load_image {{{out/"validation.bin"}}} 0x80000000 bin')
            rpc.call(f'load_image {{{out/fixture["payload"]}}} 0x80001000 bin')
            rpc.call('mww 0x80001d00 0; resume 0x80000000; sleep 50; halt')
            words=rpc.read(0x80001d00,32)
            raw_file=dest/f'reset_{i}_result.bin'
            raw_file.write_bytes(struct.pack('<32I',*words))
            signed=lambda x:x-(1<<32) if x>>31 else x
            got=list(map(signed,words[8:16]))
            if (words[0]!=0xc0dec0de or words[1] or got!=fixture['expected'] or words[3]!=2
                    or words[4]!=0x20002 or words[6] or words[31]!=2*(words[3]+words[28])):
                raise AssertionError(f'Reset recovery failed {words[:8]} logits={got}')
            report['resets'].append(dict(index=i,interrupted_active_workload=active,before=before,
                reset_state=registers,raw_reset_response=reset_log,logits=got,jobs=words[3],status='PASS',
                result_file=raw_file.name,result_sha256=digest(raw_file)))
            save(dest/'summary.json',report);print(f'PASS reset {i+1}/{args.resets} active_workload={active}',flush=True)
        report['status']='PASS'
    except BaseException as e:
        report.update(status='FAIL',error=str(e));raise
    finally:
        if rpc:
            try:rpc.call('halt')
            finally:rpc.close()
        save(dest/'summary.json',report)

def main():
    p=argparse.ArgumentParser(__doc__);sub=p.add_subparsers(dest='action',required=True)
    q=sub.add_parser('prepare');q.add_argument('--output',required=True);q.add_argument('--soak',type=int,default=100)
    q.add_argument('--tool-prefix',default='/opt/xpack-riscv-none-elf-gcc-13.2.0-2/bin/riscv-none-elf-')
    q=sub.add_parser('board');q.add_argument('--output',required=True);q.add_argument('--run-name',default='board')
    q.add_argument('--bitstream',required=True);q.add_argument('--bitstream-sha256',required=True)
    q.add_argument('--khz',type=int,default=500);q.add_argument('--timeout',type=float,default=30);q.add_argument('--limit',type=int)
    q=sub.add_parser('recovery');q.add_argument('--output',required=True);q.add_argument('--run-name',default='recovery')
    q.add_argument('--resets',type=int,default=6)
    a=p.parse_args()
    if getattr(a,'soak',1)<1 or not 1<=getattr(a,'khz',1)<=1000 or getattr(a,'resets',2)<2 or getattr(a,'timeout',1)<=0:
        p.error('Invalid repeat count, reset count, timeout or adapter speed')
    {'prepare':prepare,'board':board,'recovery':recovery}[a.action](a)
if __name__=='__main__': main()
