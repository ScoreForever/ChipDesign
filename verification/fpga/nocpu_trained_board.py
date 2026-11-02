"""Check trained-weight ABI 2.1 FPGA outputs against the integer reference."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'software/host'))
from nocpu_board_utils import encode_tensor
from nocpu_jtag import Npu, TclRpc, JtagTransport, Op
from npu_program import from_tinycnn_npz, compile_model, reference

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--model',type=Path,default=ROOT/'software/models/tinycnn8_flatten_strong.npz')
args=p.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
rpc=TclRpc(timeout=30)
report=dict(passed=False,runs=[],model_sha256=hashlib.sha256(args.model.read_bytes()).hexdigest(),
            limitation='Synthetic input tests numeric execution, not dataset classification accuracy.')
try:
    npu=Npu(JtagTransport(rpc,timeout=30)); npu.command(Op.RESET)
    model=from_tinycnn_npz(args.model)
    pkg=compile_model(model,cols=npu.read(0x58)&65535); pkg.save(args.output/'package')
    npu.load_model(pkg)
    rng=random.Random(20261023); frame=[rng.randrange(-32,33) for _ in range(320)]
    for i,values in enumerate([frame,list(reversed(frame))],1):
        inp=encode_tensor(values,'i8')
        expected=encode_tensor(reference(model,values)[model['nodes'][-1]['output']],pkg.manifest['output_dtype'])
        actual=npu.infer(inp,timeout=30)
        for label,data in [('input',inp),('expected',expected),('actual',actual)]:
            (args.output/f'{label}_{i}.bin').write_bytes(data)
        report['runs'].append(dict(matches=actual==expected,output_sha256=hashlib.sha256(actual).hexdigest()))
        if actual!=expected: raise RuntimeError('trained-weight board output mismatch')
        performance=npu.performance()
        trace=npu.trace(include_samples=False)
        (args.output/f'performance_{i}.json').write_text(json.dumps(performance,indent=2)+'\n')
        (args.output/f'trace_{i}.json').write_text(json.dumps(trace,indent=2)+'\n')
        if (len(trace['operators'])!=pkg.manifest['op_count'] or
            sum(row['mac_issues'] for row in trace['operators'])!=performance['mac_issues'] or
            performance['mac_issues']==0):
            raise RuntimeError('operator trace / performance issue counters differ')
        report['runs'][-1]['trace_performance_consistent']=True
    npu.command(Op.RESET); report['final_status']=npu.status(); report['passed']=True
finally:
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n'); rpc.close()
print(json.dumps(report,indent=2))
