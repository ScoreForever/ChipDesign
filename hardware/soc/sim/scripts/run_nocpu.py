#!/usr/bin/env python3
"""CPU-less controller, physical storage readback, recovery and JTAG regression."""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'hardware/npu/tools'))
from tinycnn8_golden import generate
sys.path.insert(0,str(ROOT/'software/host'))
from nocpu_jtag import Npu, Op, fixture


def host_trace(directory, path):
    weights,params,classes,frame=fixture(directory)
    gold=[int(x,16) for x in (directory/'golden_fc.hex').read_text().splitlines()]

    class Recorder:
        def __init__(self):self.rows=[];self.polls=0
        def exchange(self,op,addr=0,data=0):
            value=0;mask=0xffffffff
            if op==Op.START:value=1
            elif op==Op.READ:
                if addr==0:value=0x10000
                elif addr==4:
                    self.polls+=1;value=8 if self.polls>=3 else 1;mask=0x89
                elif addr==0x10:value=classes
                elif 0x100<=addr<0x100+4*classes:value=gold[(addr-0x100)//4]
                elif addr in (0x20,0x24):mask=0
                else:raise AssertionError(f'unexpected host read {addr:x}')
            self.rows.append(f'{int(op):02x} {addr:08x} {int(data)&0xffffffff:08x} {mask:08x} {value&0xffffffff:08x}')
            return value
    transport=Recorder();host=Npu(transport)
    host.load_model(weights,params,classes)
    result=host.infer(frame)
    if result['logits']!=[x if x<2**31 else x-2**32 for x in gold]:
        raise AssertionError('host signed logit conversion failed')
    path.write_text('\n'.join(transport.rows)+'\n')


def run(args, marker='', timeout=180):
    p = subprocess.run([str(x) for x in args], cwd=ROOT, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if p.returncode or marker not in p.stdout:
        raise RuntimeError(f"{' '.join(map(str,args))}\n{p.stdout[-6000:]}")
    return p.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=Path('build/nocpu_regression'))
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for tool in ('iverilog', 'vvp'):
        if not shutil.which(tool):
            raise SystemExit(f'missing {tool}')
    sources = (ROOT/'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
    results = []
    for top in ('tb_nocpu_controller', 'tb_nocpu_jtag', 'tb_nocpu_host_trace'):
        image = out / (top+'.vvp')
        tb = 'hardware/soc/sim/tb/'+top+'.sv'
        run(['iverilog', '-g2012', '-s', top, '-o', image, *sources, tb])
        for classes, seed, pattern in ((4,7,'random'),(8,6,'extreme'),(1,11,'impulse')):
            if top != 'tb_nocpu_controller' and classes != 4:
                continue
            a=out/f'{pattern}_{classes}'
            b=out/f'updated_{classes}'
            generate(a,seed,classes,pattern)
            generate(b,seed+23,classes,'random')
            # Relative ASCII paths avoid Windows Icarus Unicode path handling.
            extra=[]
            if top.endswith('host_trace'):
                trace=out/'host_trace.txt';host_trace(a,trace)
                extra=[f'+HOST_TRACE={trace.relative_to(ROOT).as_posix()}']
            text=run(['vvp',image,f'+DATA_DIR={a.relative_to(ROOT).as_posix()}',
                      f'+DATA_DIR2={b.relative_to(ROOT).as_posix()}',f'+CLASSES={classes}',*extra], 'PASS nocpu')
            (out/f'{top}_{classes}.log').write_text(text)
            line=[x for x in text.splitlines() if x.startswith('PASS nocpu')][-1]
            print(line,flush=True)
            results.append(line)
    (out/'summary.json').write_text(json.dumps({'tests':results},indent=2)+'\n')
    print('ALL CPU-LESS TESTS PASSED',flush=True)


if __name__ == '__main__':
    main()
