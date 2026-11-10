"""Replay seven golden traces on the exact staged RTL with production defines."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--sources',type=Path,required=True)
p.add_argument('--summary',type=Path,required=True)
p.add_argument('--cases',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
root=Path.cwd()
sys.path.insert(0,str(root/'hardware/npu/scripts'))
import run_program_regression as r
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
expected=json.loads(a.summary.read_text())
hashes={}
for name,digest in expected['sources_sha256'].items():
    source=a.sources/name
    raw=source.read_bytes()
    if hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()!=digest:
        raise RuntimeError('staged RTL differs beyond line endings: '+name)
    hashes[name]=hashlib.sha256(raw).hexdigest()
defines=['SYNTHESIS','CHIPDESIGN_FPGA_MEM','NPU_ARRAY_COLS=8',
         'NPU_ENABLE_WAVE_TRACE=0','NPU_ENABLE_OP_TRACE=0']
image=out/'production.vvp'
r.run(['iverilog','-g2012','-s','tb_npu_program','-o',image,
       *['-D'+d for d in defines],*[a.sources/n for n in hashes],
       'hardware/soc/sim/tb/tb_npu_program.sv'],out/'compile.log')
rows=[x for x in expected['tests'] if x['case'].startswith('packaged_') and x['case'].endswith('_c8_f1_j0')]
if len(rows)!=7:raise ValueError('seven model cases required')
def replay(row):
    name=row['case'];case=out/name;case.mkdir();lines=[]
    for line in (a.cases/name/'commands.txt').read_text().splitlines():
        fields=line.split();op,addr,error=[int(fields[i],16) for i in (0,1,3)]
        if op==1 and error==0:
            if 0x200<=addr<0x1200 or 0x2000<=addr<0x6000:
                fields[3:]=['02','00000000','00000000']
            elif addr==0x184:fields[5]='00000000'
        lines.append(' '.join(fields))
    lines.append('01 000001a8 0 0 ffffffff 00000000')
    commands=case/'commands.txt';commands.write_text('\n'.join(lines)+'\n')
    log=r.run(['vvp',image,f'+TRACE={commands.relative_to(root).as_posix()}',
               f'+EVENTS={(a.cases/name/"writes.hex").resolve().relative_to(root).as_posix()}',
               f'+EVENT_COUNT={row["checked_write_events"]}'],case/'rtl.log')
    if 'PASS program' not in log:raise RuntimeError('missing completion: '+name)
    metrics=re.findall(r'METRIC reg=([0-9a-f]+) value=(\d+)',log)
    cycles=[int(v) for n,v in metrics if int(n,16)==0x38]
    writes=[int(v) for n,v in metrics if int(n,16)==0x40]
    perf=[int(v) for _,v in re.findall(r'PERF reg=([0-9a-f]+) value=(\d+)',log)]
    if cycles!=row['cycles'] or writes!=row['writes'] or perf!=sum(row['performance'],[]):
        raise RuntimeError('production execution metrics changed: '+name)
    print('PASS production',name,cycles,flush=True)
    return dict(case=name,cycles=cycles,writes=writes,performance=[perf[:24],perf[24:]],
                checked_write_events=row['checked_write_events'])
with ThreadPoolExecutor(4) as pool:results=list(pool.map(replay,rows))
(out/'summary.json').write_text(json.dumps(dict(tests=results,sources_sha256=hashes,
    defines=defines,line_endings='Raw staged files replayed; CRLF/LF normalization checked against local regression'),indent=2)+'\n')
print('ALL 7 STAGED PRODUCTION MODELS PASSED',flush=True)
