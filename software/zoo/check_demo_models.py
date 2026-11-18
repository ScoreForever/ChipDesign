"""Verify demo QAT checkpoints re-export to the exact published ABI 5 images."""

import hashlib
import json
from pathlib import Path
import sys

import torch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'software'),str(ROOT/'software/host')]
from zoo.qat import Net,annotate_shapes,export
from zoo.train_demo_vision import EMOTION_SPEC,FACE_SPEC
from npu_program import compile_model,Package


def main():
    torch.set_num_threads(2)
    result={}
    for name,spec,shape in (('face',FACE_SPEC,(32,32,1)),('emotion',EMOTION_SPEC,(48,48,1))):
        directory=ROOT/'software/host/demos/models'/name
        model=json.loads((directory/'model.json').read_text(encoding='utf-8'))
        net=Net(spec,shape)
        annotate_shapes(net)
        net.to_qat()
        net.load_state_dict(torch.load(directory/'qat_state.pt',map_location='cpu',weights_only=True))
        net.eval()
        metadata={k:v for k,v in model['metadata'].items() if k not in ('name','input_scale','output_scale')}
        rebuilt=export(net,name,metadata)
        package=compile_model(rebuilt)
        original=Package.load(directory/'c8')
        if package.image!=original.image or rebuilt!=model:
            raise AssertionError(f'{name}: checkpoint/export differs')
        result[name]=dict(passed=True,model_sha256=hashlib.sha256(package.image).hexdigest(),
                          model_bytes=len(package.image),torch=torch.__version__)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
