"""Record clean reset state and verify fresh INT8/INT32 inference on real FPGA."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'software/host'))
from nocpu_board_utils import encode_tensor
from nocpu_jtag import Npu, JtagTransport, TclRpc, Op
from npu_program import compile_model, reference

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--reset-scope',choices=['global','protocol'],default='global',
               help='Global reset requires job=0; protocol reset preserves the job counter.')
args=p.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
rpc=TclRpc(); original=rpc.execute
def logged(command):
    result=original(command)
    with (args.output/'commands.jsonl').open('a') as f:
        f.write(json.dumps(dict(time=time.time(),command=command,result=result))+'\n')
    return result
rpc.execute=logged
report=dict(passed=False,reset_scope=args.reset_scope)
try:
    rpc.execute('jtag arp_init'); npu=Npu(JtagTransport(rpc))
    report['initial_status']=npu.status(); report['job']=npu.read(0x2c)
    if any(report['initial_status'].values()) or (args.reset_scope=='global' and report['job']!=0):
        raise RuntimeError('initial state is not globally reset; no protocol reset was sent')
    values=[-8,7,-1,0,4]
    model=dict(input=dict(name='x',shape=[1,1,5]),nodes=[
        dict(op='FC',input='x',output='y',channels=3,weights=[1,-2,3,-4,5]*3,bias=[0,17,-31])])
    package=compile_model(model,cols=npu.read(0x58)&65535)
    package.save(args.output/'package')
    inp=encode_tensor(values,'i8')
    expected=encode_tensor(reference(model,values)['y'],package.manifest['output_dtype'])
    npu.load_model(package); actual=npu.infer(inp)
    (args.output/'input.bin').write_bytes(inp); (args.output/'expected.bin').write_bytes(expected)
    (args.output/'actual.bin').write_bytes(actual)
    report['actual_hex']=actual.hex(); report['expected_hex']=expected.hex()
    if actual!=expected: raise RuntimeError('fresh MAC inference mismatch')
    trace=npu.trace(include_samples=True)
    (args.output/'trace.json').write_text(json.dumps(trace,indent=2)+'\n')
    if not trace['samples'] or len(trace['operators'])!=1:
        raise RuntimeError('fresh job waveform/operator trace missing')
    report['internal_trace_samples']=len(trace['samples'])
    report['trace_timestamps_checked']=True
    npu.command(Op.RESET); report['final_status']=npu.status(); report['passed']=True
finally:
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n'); rpc.close()
print(json.dumps(report,indent=2))
