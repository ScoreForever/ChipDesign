"""Generate a complete small-CNN example, new ABI package and golden output."""
from pathlib import Path
import argparse
import json
import random
import struct
from npu_program import compile_model,reference

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=Path('build/npu_demo'))
    args=p.parse_args();r=random.Random(23)
    model=dict(input=dict(name='input',shape=[8,8,1]),nodes=[
        dict(op='Conv',input='input',output='conv',kernel=3,pads=[1]*4,channels=4,
             weights=[r.randrange(-4,5) for _ in range(36)],bias=[0,3,-2,1]),
        dict(op='Requant',input='conv',output='quant',multiplier=[0x40000000],shift=[-3]),
        dict(op='ReLU',input='quant',output='relu'),dict(op='MaxPool',input='relu',output='pool'),
        dict(op='FC',input='pool',output='output',channels=3,
             weights=[r.randrange(-3,4) for _ in range(64*3)],bias=[1,-2,3])])
    values=[r.randrange(-128,128) for _ in range(64)];args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'model.json').write_text(json.dumps(model,indent=2)+'\n')
    (args.output/'input.bin').write_bytes(bytes(v&255 for v in values))
    expected=reference(model,values)['output']
    (args.output/'expected.bin').write_bytes(struct.pack('<3i',*expected))
    compile_model(model).save(args.output/'package')
    print(json.dumps(dict(output=str(args.output),expected=expected),indent=2))
if __name__=='__main__':main()
