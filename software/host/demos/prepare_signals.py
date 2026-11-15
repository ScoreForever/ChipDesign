"""Reproduce HAR training normalization and verify all published raw HAR frames."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import torch

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HOST))
from demos.signals import AXES, MODELS, open_uci_zip, prepare_har


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset",type=Path)
    p.add_argument("--output",type=Path,default=Path(__file__).with_name("har_normalization.json"))
    args=p.parse_args()
    with open_uci_zip(args.dataset) as archive:
        def load(relative):
            name,=[n for n in archive.namelist() if n.endswith("UCI HAR Dataset/"+relative)]
            with archive.open(name) as handle:
                return np.loadtxt(handle)
        # Match data.uci_har: float32 N,C,H,W and sample std over train axes.
        train=np.stack([load(f"train/Inertial Signals/{a}_train.txt") for a in AXES],axis=1)
        tensor=torch.tensor(train,dtype=torch.float32).unsqueeze(2)
        mean=tensor.mean((0,2,3)).tolist()
        std=tensor.std((0,2,3)).tolist()
        test=np.stack([load(f"test/Inertial Signals/{a}_test.txt") for a in AXES],axis=-1).astype(np.float32)
    golden=json.loads((MODELS/'har/golden.json').read_text())
    stats=(np.array(mean,dtype=np.float32),np.array(std,dtype=np.float32))
    checks=[]
    for frame in golden['frames']:
        values=prepare_har(test[frame['index']],golden['input_scale'],stats)
        if values!=frame['input']:
            raise AssertionError(f"HAR preprocessing differs from golden {frame['index']}: {sum(a!=b for a,b in zip(values,frame['input']))} elements")
        checks.append(frame['index'])
    with args.dataset.open('rb') as handle:
        digest=hashlib.file_digest(handle,'sha256').hexdigest()
    report=dict(mean=mean,std=std,axes=list(AXES),train_windows=len(train),std_correction=1,
                source="UCI HAR official train split, all 7352 windows; same as software/zoo/data.py",
                url="https://archive.ics.uci.edu/dataset/240/human+activity+recognition+using+smartphones",
                archive_sha256=digest,verified_golden_indices=checks)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(f"HAR normalization saved; {len(checks)} raw test windows match published INT4 inputs exactly")


if __name__=='__main__':
    main()
