#!/usr/bin/env python3
"""RTL-only area/performance variants; no synthesis or place-and-route.

Checks pooled tensors, logits, every array/loader/quantizer transaction and
back-to-back inference. Boundary fixtures use an independent canonical oracle.
--macro-model optionally runs the actual course SRAM Verilog, kept outside Git.
"""
from pathlib import Path
import argparse
import hashlib
import json
import random
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'hardware/npu/tools'))
import tinycnn8_golden as golden
from check_fused_events import check_trace, read_hex

SOURCES=[f'hardware/npu/rtl/{n}.sv' for n in
         ('fused_ws_array','requant_lane','npu_weight_store','tinycnn8_npu_fused')]
TB='hardware/npu/tb/tb_tinycnn8_fused_golden.sv'
PROFILES={
    'ff_wide':(0,16,32,32),
    'ff_narrow':(0,16,18,23),
    'sram16_wide':(1,16,32,32),
    'sram16':(1,16,18,23),
    'sram8':(1,8,18,23),
    'sram4':(1,4,18,23),
}

def run(args, log):
    p=subprocess.run([str(x) for x in args],cwd=ROOT,text=True,
                     stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    log.write_text(p.stdout,encoding='utf-8')
    if p.returncode:
        raise RuntimeError(f'{args[0]} failed; see {log}\n{p.stdout[-3000:]}')
    return p.stdout

def boundary_fixture(path, seed, mode):
    model=golden.build_model(seed,8,'extreme')
    rng=random.Random(seed)
    edges=[-(1<<31),(1<<31)-1,(1<<31)-101,-(1<<31)+101,-1,0,1,123456789]
    model['input']=[rng.choice([-128,127,0,-1,1]) for _ in range(320)]
    for layer in ('conv1','conv2'):
        cfg=model[layer]
        for oc,channel in enumerate(cfg['weights']):
            for row in channel:
                for tap in row:
                    for ic in range(len(tap)):
                        tap[ic]=rng.choice([-128,127]) if mode=='overflow' else (-128 if layer=='conv1' else 127)
        cfg['bias']=edges[:] if mode=='overflow' else [0]*8
        cfg['multiplier']=[0x7fffffff]*8
        cfg['shift']=[-12]*8 if mode=='overflow' else [-10]*8
    model['fc']['bias']=edges[:]
    model['fc']['weights']=[[rng.choice([-128,127]) for _ in range(160)] for _ in range(8)]
    if mode=='dot_bound':
        model['input']=[-128]*320
        model['fc']['weights']=[[-128 if oc%2==0 else 127]*160 for oc in range(8)]
    path.mkdir(parents=True,exist_ok=True)
    golden._hex_file(path,'input.hex',model['input'],8)
    golden._hex_file(path,'weights.hex',golden.pack_weights(model),64)
    for name,values,bits in zip(('bias','multiplier','shift'),golden.parameter_arrays(model),(32,32,8)):
        golden._hex_file(path,name+'.hex',values,bits)
    expected=golden.run_model(model)
    for name,values in expected.items():
        golden._hex_file(path,'golden_'+name+'.hex',values,32 if name=='fc' else 8)
    (path/'model.json').write_text(json.dumps(model,indent=2)+'\n')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=Path('build/area_experiments'))
    ap.add_argument('--seeds',type=int,default=8)
    ap.add_argument('--profiles',nargs='+',choices=PROFILES,default=list(PROFILES))
    ap.add_argument('--macro-model',type=Path)
    args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    fixtures=[]
    for seed in range(1,args.seeds+1):
        classes=1+(seed-1)%8
        path=out/f'random_{seed}_{classes}'
        golden.generate(path,seed,classes,'random')
        fixtures.append((path,classes))
    for seed,classes,pattern in ((11,6,'impulse'),(6,8,'extreme')):
        path=out/f'{pattern}_{seed}_{classes}';golden.generate(path,seed,classes,pattern)
        fixtures.append((path,classes))
    for mode in ('overflow','dot_bound'):
        path=out/mode;boundary_fixture(path,125,mode);fixtures.append((path,8))
    summary={'profiles':{},'units':[],'macro_model':None,'rtl_only':True}
    if args.macro_model:
        summary['macro_model']={'name':args.macro_model.name,
                               'sha256':hashlib.sha256(args.macro_model.read_bytes()).hexdigest()}
    for width in (8,16,32,64):
        image=out/f'weight_store_{width}.vvp'
        cmd=['iverilog','-g2012','-s','tb_npu_weight_store','-o',image,
             f'-Ptb_npu_weight_store.DATA_WIDTH={width}']
        src=['hardware/npu/rtl/npu_weight_store.sv','hardware/npu/tb/tb_npu_weight_store.sv']
        if args.macro_model:
            cmd+=['-DNPU_USE_RA1SHD_WEIGHT'];src+=[args.macro_model.resolve()]
        run(cmd+src,out/f'weight_store_{width}_compile.log')
        text=run(['vvp',image],out/f'weight_store_{width}.log')
        if 'PASS weight_store' not in text: raise AssertionError(text[-2000:])
        summary['units'].append(text.strip().splitlines()[-1])
        print(summary['units'][-1],flush=True)
    for name in args.profiles:
        sram,tile,acc,psum=PROFILES[name]
        image=out/(name+'.vvp')
        cmd=['iverilog','-g2012','-s','tb_tinycnn8_fused_golden','-o',image,'-DTINYCNN8_FUSED_EVENTS']
        cmd += [f'-Ptb_tinycnn8_fused_golden.{key}={value}' for key,value in
                zip(('WEIGHT_SRAM','SPATIAL_TILE','ARRAY_ACC_WIDTH','PSUM_WIDTH'),(sram,tile,acc,psum))]
        sources=SOURCES+[TB]
        if args.macro_model and sram:
            cmd+=['-DNPU_USE_RA1SHD_WEIGHT'];sources+=[args.macro_model.resolve()]
        run(cmd+sources,out/(name+'_compile.log'))
        records=[]
        for path,classes in fixtures:
            trace=out/f'{name}_{path.name}.csv'
            text=run(['vvp',image,f'+DATA_DIR={path.relative_to(ROOT).as_posix()}',
                      f'+CLASSES={classes}',f'+EVENT_TRACE={trace.relative_to(ROOT).as_posix()}'],
                     out/f'{name}_{path.name}.log')
            if 'PASS fused golden' not in text: raise AssertionError(text[-2000:])
            jobs=check_trace(trace,[read_hex(path/'input.hex')],read_hex(path/'weights.hex'),
                             read_hex(path/'bias.hex'),read_hex(path/'multiplier.hex'),
                             read_hex(path/'shift.hex'),classes,spatial_tile=tile,
                             expected_phase_cycles=(1000,1480,325) if not sram and tile==16 else None)
            if len(jobs)!=2 or jobs[0]['cycles']!=jobs[1]['cycles']: raise AssertionError('job reuse mismatch')
            records.append({'case':path.name,'classes':classes,'jobs':jobs})
        summary['profiles'][name]={'parameters':dict(zip(('weight_sram','tile','array_acc','psum'),
                                                       (sram,tile,acc,psum))),
                                   'cases':records,'cycles':records[0]['jobs'][0]['cycles'],
                                   'phase_cycles':records[0]['jobs'][0]['phase_cycles']}
        print(f"PASS {name}: {len(records)} cases x 2 jobs; cycles={summary['profiles'][name]['cycles']} "
              f"phases={summary['profiles'][name]['phase_cycles']}",flush=True)
        (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    base=summary['profiles'].get('ff_wide',{}).get('cycles',2805)
    for entry in summary['profiles'].values():
        entry['cycle_increase_percent']=round(100*(entry['cycles']/base-1),3)
    if 'sram16' in summary['profiles'] and summary['profiles']['sram16']['cycles']>base*1.10:
        raise AssertionError('default SRAM/tile16 profile exceeds the 10% core-cycle budget')
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('ALL AREA EXPERIMENTS PASSED',flush=True)

if __name__=='__main__': main()
