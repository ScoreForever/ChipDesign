"""Replay independent tensor golden events with the exact FPGA trace-off defines.

Uses completed full regression inputs, then recompiles RTL for production and
checks every tensor write, all final reads, cycles/writes and 24 performance
counters. Trace-disabled address reads must report unavailable, not stale data.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'hardware/npu/scripts'))
import run_program_regression as R


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-evidence',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--jobs',type=int,default=4)
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    source=args.source_evidence.resolve()
    baseline=json.loads((source/'summary.json').read_text())
    for name,expected in baseline['sources_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('golden evidence is for a different RTL: '+name)
    sources=(ROOT/'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
    production=['-DSYNTHESIS','-DCHIPDESIGN_FPGA_MEM','-DNPU_ENABLE_WAVE_TRACE=0','-DNPU_ENABLE_OP_TRACE=0']
    images={}
    for cols,jtag in [(8,False),(4,False),(8,True)]:
        image=out/f'program_{cols}_{jtag}.vvp'
        ds=production+([f'-DNPU_ARRAY_COLS={cols}','-DCOLS4'] if cols==4 else ['-DNPU_ARRAY_COLS=8'])
        if jtag: ds.append('-DJTAG_TEST')
        R.run(['iverilog','-g2012','-s','tb_npu_program','-o',image,*ds,*sources,
               'hardware/soc/sim/tb/tb_npu_program.sv'],out/f'compile_{cols}_{jtag}.log')
        images[cols,jtag]=image
    def one(row):
        name=row['case'];cols=int(re.search(r'_c(\d+)_',name).group(1));jtag=name.endswith('_j1')
        case=out/name;case.mkdir();lines=[]
        for line in (source/name/'commands.txt').read_text().splitlines():
            fields=line.split();op,addr,error=[int(fields[i],16) for i in (0,1,3)]
            if op==1 and error==0:
                if 0x200<=addr<0x400 or 0x1000<=addr<0x5000:
                    fields[3:]=['02','00000000','00000000']
                elif addr==0x184: fields[5]='00000000'
            lines.append(' '.join(fields))
        # Explicitly verify that the operator bank is absent as compiled.
        lines.append('01 000001a8 0 0 ffffffff 00000000')
        commands=case/'commands.txt';commands.write_text('\n'.join(lines)+'\n')
        log=R.run(['vvp',images[cols,jtag],f'+TRACE={commands.relative_to(ROOT).as_posix()}',
                   f'+EVENTS={(source/name/"writes.hex").relative_to(ROOT).as_posix()}',
                   f'+EVENT_COUNT={row["checked_write_events"]}'],case/'rtl.log')
        if 'PASS program' not in log: raise RuntimeError('missing completion: '+name)
        metric=re.findall(r'METRIC reg=([0-9a-f]+) value=(\d+)',log)
        cycles=[int(v) for a,v in metric if int(a,16)==0x38]
        writes=[int(v) for a,v in metric if int(a,16)==0x40]
        perf=[int(v) for _,v in re.findall(r'PERF reg=([0-9a-f]+) value=(\d+)',log)]
        if cycles!=row['cycles'] or writes!=row['writes'] or perf!=sum(row['performance'],[]):
            raise RuntimeError('trace-off changed execution metrics: '+name)
        return dict(case=name,cycles=cycles,writes=writes,checked_write_events=row['checked_write_events'],passed=True)
    with ThreadPoolExecutor(args.jobs) as pool: results=list(pool.map(one,baseline['tests']))
    (out/'summary.json').write_text(json.dumps(dict(passed=True,defines=production,tests=results,
        sources_sha256=baseline['sources_sha256']),indent=2)+'\n')
    print('PASS production trace-off:',len(results),'cases, every tensor write and complete metrics unchanged')


if __name__=='__main__':main()
