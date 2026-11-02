"""Continuously run jobs until an operator presses RST/S6. No RTL changes.

Only a reset bracketed by a last valid BUSY sample is counted as coverage.
This is a sampled functional observation, not edge-level waveform evidence.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import struct

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'software/host'))
from nocpu_board_utils import encode_tensor
from nocpu_jtag import Npu, TclRpc, JtagTransport, Op
from npu_program import compile_model, Package, reference

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--seconds',type=int,default=180)
args=p.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
rpc=TclRpc(timeout=30); original=rpc.execute
def logged(command):
    result=original(command)
    with (args.output/'commands.jsonl').open('a') as f:
        f.write(json.dumps(dict(time=time.time(),command=command,result=result))+'\n')
    return result
rpc.execute=logged
rpc.execute('jtag arp_init')
tx=JtagTransport(rpc); npu=Npu(tx)
report=dict(passed=False,last_valid_status=None,scope='sampled busy immediately preceding physical reset; no waveform')
last=None
def burst(operations):
    commands=['set finished 0', 'set rr {}']
    for i,(op,addr,data) in enumerate(operations):
        body=[f'drscan npu.tap 32 0x{data:x} 32 0x{addr:x} 8 0x{int(op):x} -endstate IDLE',
                     'drscan npu.tap 32 0 32 0 8 0 -endstate IDLE',
                     f'set r{i} [drscan npu.tap 32 0 32 0 8 0 -endstate IDLE]',f'lappend rr $r{i}']
        if op==Op.READ and addr==4:
            body += [f'scan [lindex $r{i} 0] %x state', 'if {$state & 8} {set finished 1}']
        commands.append('if {!$finished} {'+'; '.join(body)+'}')
    commands.append('join $rr |')
    return [[int(v,16) for v in s.split()] for s in rpc.execute('; '.join(commands)).split('|')]
try:
    npu.command(Op.RESET)
    nodes=[]; src='x'
    for i in range(16):
        channels=16
        weights=[0]*(channels*9*channels)
        for c in range(channels): weights[c*9*channels+4*channels+c]=1
        nodes += [dict(op='Conv',input=src,output=f'a{i}',channels=channels,kernel=3,pads=[2 if i<5 else 1]*4,weights=weights,bias=[0]*channels),
                  dict(op='Requant',input=f'a{i}',output=f'q{i}',multiplier=[0x40000000]*channels,shift=[1]*channels)]
        src=f'q{i}'
    model=dict(input=dict(name='x',shape=[1,1,16]),nodes=nodes)
    package=compile_model(model,cols=npu.read(0x58)&65535,model_bytes=200000)
    # Legal descriptors may share identical immutable weight/parameter blocks.
    # Deduplicate compiler output to fit the physical 8 KiB model SRAM.
    image=bytearray(16*64); blocks={}
    for i in range(16):
        words=list(struct.unpack_from('<16I',package.image,i*64))
        for field,size in [(7,2304),(8,192)]:
            payload=package.image[words[field]:words[field]+size]
            if len(payload)!=size: raise RuntimeError('invalid parameter block')
            if payload not in blocks:
                blocks[payload]=len(image); image.extend(payload)
            words[field]=blocks[payload]
        struct.pack_into('<16I',image,i*64,*words)
    if len(image)>8192: raise RuntimeError('deduplicated model exceeds SRAM')
    package.manifest['model_bytes']=len(image)
    package=Package(bytes(image),package.manifest)
    (args.output/'model.json').write_text(json.dumps(model))
    package.save(args.output/'package'); npu.load_model(package)
    npu.write(0x28,1_000_000_000)
    inp=encode_tensor([7]*16,'i8')
    (args.output/'input.bin').write_bytes(inp)
    expected=encode_tensor(reference(model,[7]*16)[src],package.manifest['output_dtype'])
    actual=npu.infer(inp,timeout=30)
    (args.output/'expected.bin').write_bytes(expected)
    (args.output/'pre_reset_actual.bin').write_bytes(actual)
    if actual!=expected: raise RuntimeError('shared-weight workload failed golden comparison')
    report['pre_reset_inference_matches']=True
    print('ARMED: continuous job loop active; operator may press RST/S6 once',flush=True)
    deadline=time.monotonic()+args.seconds
    jobs=0
    while time.monotonic()<deadline:
        ops=([(Op.ACK,0,0)] if jobs else [])+[(Op.INPUT_BEGIN,0,0)]
        ops += [(Op.WRITE,0x20000+package.manifest['input_base']+i,int.from_bytes(inp[i:i+4].ljust(4,b'\0'),'little'))
             for i in range(0,len(inp),4)]
        ops += [(Op.INPUT_COMMIT,0,0),(Op.START,0,0)]
        replies=burst(ops+[(Op.READ,4,0)]*32)
        if any(len(r)!=3 or r[2]!=128 for r in replies[:len(ops)]):
            raise RuntimeError('disturbance between jobs')
        replies=replies[len(ops):]
        jobs+=1
        while True:
            for r in replies:
                if len(r)!=3 or r[2]!=128 or r[1]!=4: raise RuntimeError('TAP response interrupted')
                last=r[0]; report['last_valid_status']=last
                if last&64: raise RuntimeError('job fault')
            if last&8: break
            replies=burst([(Op.READ,4,0)]*32)
    raise RuntimeError('operator window expired without reset')
except Exception as exc:
    report['interruption']=str(exc); report['observed_at']=time.time()
    try:
        # Allow the operator's one-second button hold to finish before TAP init.
        time.sleep(2)
        rpc.execute('jtag arp_init'); npu=Npu(JtagTransport(rpc))
        report['after']=npu.status(); report['job_after']=npu.read(0x2c)
        report['timeout_after']=npu.read(0x28)
        report['passed']=bool(last is not None and last&1 and report['job_after']==0 and
                              report['timeout_after']==10000000 and not any(report['after'].values()))
    except Exception as recovery:
        report['recovery_error']=str(recovery)
finally:
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    rpc.close()
print(json.dumps(report,indent=2))
if not report['passed']: sys.exit(2)
