"""Executable feasibility study: row-buffer two Conv+Requant+ReLU stages.

This is a software scheduling experiment, not an implemented RTL streaming mode,
cycle estimate, synthesis or PPA result. It counts the additional weight-tile
loads implied by row boundaries under the current K-major spatial tiling.
"""
from pathlib import Path
import argparse
import json
import random
from npu_program import reference,requant

def explore(h=8,w=8,ci=2,cm=4,co=3):
    r=random.Random(23)
    wa=[r.randrange(-3,4) for _ in range(cm*9*ci)]
    wb=[r.randrange(-3,4) for _ in range(co*9*cm)]
    source=[r.randrange(-8,8) for _ in range(h*w*ci)]
    model=dict(input=dict(name='x',shape=[h,w,ci]),nodes=[
        dict(op='Conv',input='x',output='a',channels=cm,kernel=3,pads=[1]*4,weights=wa,bias=[1]*cm),
        dict(op='Requant',input='a',output='b',multiplier=[0x4000],shift=[-2]),
        dict(op='ReLU',input='b',output='c'),
        dict(op='Conv',input='c',output='d',channels=co,kernel=3,pads=[1]*4,weights=wb,bias=[-1]*co),
        dict(op='Requant',input='d',output='e',multiplier=[0x4000],shift=[-3]),
        dict(op='ReLU',input='e',output='y')])
    expected=reference(model,source)['y'];cache={};actual=[];peak=0;produced=0;switches=0;mode=None
    def switch(new):
        nonlocal switches,mode
        if mode is not None and mode!=new:switches+=1
        mode=new
    def producer_row(y):
        nonlocal produced
        switch('conv1');row=[]
        for x in range(w):
            for o in range(cm):
                acc=1
                for ky in range(3):
                    for kx in range(3):
                        iy=y+ky-1;ix=x+kx-1
                        if 0<=iy<h and 0<=ix<w:
                            for c in range(ci):acc+=source[(iy*w+ix)*ci+c]*wa[o*9*ci+(ky*3+kx)*ci+c]
                row.append(max(0,requant(acc,0x4000,-2)))
        produced+=1;return row
    for y in range(h):
        required={iy for iy in (y-1,y,y+1) if 0<=iy<h}
        for old in list(cache):
            if old not in required:del cache[old]
        for iy in sorted(required):
            if iy not in cache:cache[iy]=producer_row(iy)
        peak=max(peak,sum(len(row) for row in cache.values()));switch('conv2')
        for x in range(w):
            for o in range(co):
                acc=-1
                for ky in range(3):
                    for kx in range(3):
                        iy=y+ky-1;ix=x+kx-1
                        if 0<=iy<h and 0<=ix<w:
                            for c in range(cm):acc+=cache[iy][ix*cm+c]*wb[o*9*cm+(ky*3+kx)*cm+c]
                actual.append(max(0,requant(acc,0x4000,-3)))
    assert actual==expected and produced==h
    def loads(spatial_tiles,k,oc):
        row_bytes=sum(2 if oc-base<=4 else 4 for base in range(0,oc,8))
        return spatial_tiles*((k+3)//4)*4*row_bytes
    return dict(scope='software-only feasibility; no RTL timing/area claim',shape=[h,w,ci,cm,co],
                comparisons=len(expected),producer_rows=produced,intermediate_full_bytes=(h*w*cm+1)//2,
                row_cache_peak_bytes=(peak+1)//2,intermediate_write_read_bytes_avoided=2*((h*w*cm+1)//2),
                layerwise_weight_switches=1,row_schedule_weight_switches=switches,
                layerwise_weight_load_bytes=loads((h*w+15)//16,9*ci,cm)+loads((h*w+15)//16,9*cm,co),
                row_schedule_weight_load_bytes=loads(h*((w+15)//16),9*ci,cm)+loads(h*((w+15)//16),9*cm,co))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();results=[explore(w=w) for w in (8,16,32)]
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))
if __name__=='__main__':main()
