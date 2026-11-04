#!/usr/bin/env python3
"""Generate a deterministic nonzero physical-JTAG trace from the host oracle.

The u4_chain fixture covers a fused Conv+Requant+ReLU+MaxPool, depthwise
convolution, an in-place residual Add, AvgPool, a general MaxPool and u4
tensors; model, input and output move through the JTAG burst IRs."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'hardware/npu/scripts'))
from run_program_regression import fixtures, Recorder, encoded
from npu_program import compile_model, reference
from nocpu_jtag import Npu

def main(output,cols=8):
    _,model,frame=next(case for case in fixtures() if case[0]=='u4_chain')
    package=compile_model(model,cols=cols,fuse=True)
    values=reference(model,frame)[model['nodes'][-1]['output']]
    expected=encoded(values,package.manifest['output_dtype'])
    if not any(expected):raise RuntimeError('nonzero fixture required')
    recorder=Recorder(package,expected);host=Npu(recorder)
    host.load_model(package)
    for _ in range(2):
        assert host.infer(encoded(frame,model['input'].get('dtype','i4')))==expected
    recorder.rows+=['09 0 0 0 0 0','01 0 0 0 ffffffff 00040000']
    Path(output).write_text('\n'.join(recorder.rows)+'\n')
if __name__=='__main__':main(sys.argv[1],int(sys.argv[2]) if len(sys.argv)>2 else 8)
