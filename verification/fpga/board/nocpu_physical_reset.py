"""Arm/check a human-operated reset with a non-default register sentinel.

Between arm and check, issue no JTAG commands and press the P15 system reset.
The check sends TAP reset only; it never sends protocol RESET or reprograms.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'software/host'))
from nocpu_jtag import TclRpc, JtagTransport, Npu, Op
from npu_program import compile_model, encode_tensor

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('phase',choices=['arm','check'])
p.add_argument('--output',type=Path,required=True)
args=p.parse_args()
args.output.mkdir(parents=True,exist_ok=True)
rpc=TclRpc()
original=rpc.execute
def logged(command):
    result=original(command)
    with (args.output/'commands.jsonl').open('a') as f:
        f.write(json.dumps(dict(time=time.time(),command=command,result=result))+'\n')
    return result
rpc.execute=logged
try:
    if args.phase=='check': rpc.execute('jtag arp_init')
    npu=Npu(JtagTransport(rpc))
    if args.phase=='arm':
        status=npu.status()
        if status['busy']: raise RuntimeError('finish automatic suite before arming')
        npu.write(0x28,123456789)
        if npu.read(0x28)!=123456789: raise RuntimeError('sentinel write failed')
        record=dict(armed=True,time=time.time(),sentinel=123456789,status=status,job=npu.read(0x2c))
    else:
        before=json.loads((args.output/'arm.json').read_text())
        timeout=npu.read(0x28); status=npu.status(); job=npu.read(0x2c)
        passed=before['armed'] and timeout==10000000 and job==0 and not any(status.values())
        record=dict(passed=passed,time=time.time(),timeout_limit=timeout,status=status,job=job,
                    interpretation='Physical reset inferred from cleared sentinel and state; operator confirmation required.')
        if passed:
            model=dict(input=dict(name='x',shape=[1,1,17]),nodes=[dict(op='ReLU',input='x',output='y')])
            values=[-8,-7,-1,0,1,6,7]*2+[-8,0,7]
            package=compile_model(model,cols=npu.read(0x58)&65535)
            npu.load_model(package)
            actual=npu.infer(encode_tensor(values,'i4'))
            expected=encode_tensor([max(0,v) for v in values],'i4')
            record['fresh_inference_matches']=actual==expected
            record['actual_hex']=actual.hex(); record['expected_hex']=expected.hex()
            record['passed']=passed and actual==expected
            npu.command(Op.RESET)
    (args.output/f'{args.phase}.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))
    if args.phase=='check' and not record['passed']: raise RuntimeError('physical reset evidence failed')
finally: rpc.close()
