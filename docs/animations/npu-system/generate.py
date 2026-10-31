"""Regenerate offline teaching data from the repository compiler and actual RTL.

Run from any directory: python docs/animations/npu-system/generate.py
Requires Python standard library, iverilog and vvp. No private technology IP.
"""
from pathlib import Path
import csv
import hashlib
import json
import re
import struct
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'hardware/npu/scripts'))
from run_program_regression import Recorder, encoded
from npu_program import compile_model,reference
from nocpu_jtag import Npu

def run(args):
    p=subprocess.run([str(v) for v in args],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',timeout=180)
    if p.returncode or 'FATAL:' in p.stdout:raise RuntimeError(p.stdout)
    return p.stdout

def main():
    out=ROOT/'build/system_animation';out.mkdir(parents=True,exist_ok=True)
    run([sys.executable,'software/host/make_demo.py','--output',out/'demo'])
    model=json.loads((out/'demo/model.json').read_text())
    frame=list(struct.unpack('64b',(out/'demo/input.bin').read_bytes()))
    logical=reference(model,frame)
    sources=(ROOT/'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
    image=out/'animation.vvp'
    compile_log=run(['iverilog','-g2012','-s','tb_npu_program','-s','animation_capture','-o',image,*sources,
                     'hardware/soc/sim/tb/tb_npu_program.sv',HERE/'capture.sv'])
    (out/'compile.log').write_text(compile_log,encoding='utf-8')
    data=dict(model=model,input=frame,logical=logical,variants={})
    for fused in (True,False):
        key='fused' if fused else 'plain';case=out/key;case.mkdir(exist_ok=True)
        pkg=compile_model(model,fuse=fused);pkg.save(case)
        expected=encoded(logical['output'],pkg.manifest['output_dtype'])
        rec=Recorder(pkg,expected);host=Npu(rec);host.load_model(pkg)
        assert host.infer(encoded(frame,'i8'))==expected
        rec.rows.extend(['01 38 0 0 0 0','01 40 0 0 0 0'])
        rec.rows.extend(f'01 {0x200+4*i:x} 0 0 0 0' for i in range(pkg.manifest['op_count']*8))
        commands=case/'commands.txt';commands.write_text('\n'.join(rec.rows)+'\n')
        events=[]
        for name,info in list(pkg.manifest['tensors'].items())[1:]:
            vals=logical[name];channels=info['shape'][2];size=4 if info['dtype']=='i32' else 1
            for oc in range(0,channels,8):
                for pos in range(len(vals)//channels):
                    for lane in range(min(8,channels-oc)):
                        idx=pos*channels+oc+lane;addr=info['base']+idx*size;mask=15 if size==4 else 1
                        events.append((addr<<48)|(mask<<32)|(vals[idx]&0xffffffff))
        ev=case/'writes.hex';ev.write_text(''.join(f'{x:016x}\n' for x in events))
        capture=case/'cycles.csv'
        log=run(['vvp',image,f'+TRACE={commands.relative_to(ROOT).as_posix()}',
                 f'+EVENTS={ev.relative_to(ROOT).as_posix()}',f'+EVENT_COUNT={len(events)}',
                 f'+CAPTURE={capture.relative_to(ROOT).as_posix()}'])
        assert 'PASS program' in log
        (case/'rtl.log').write_text(log,encoding='utf-8')
        with capture.open() as f:
            rows=list(csv.reader(f));columns=rows.pop(0)
            cycles=[[None if re.search('[xz]',x,re.I) else int(x) for x in row] for row in rows]
        opvalues=[int(v) for v in re.findall(r'OPTRACE reg=[0-9a-f]+ value=(\d+)',log)]
        ops=[opvalues[i:i+8] for i in range(0,len(opvalues),8)]
        metrics={int(a,16):int(v) for a,v in re.findall(r'METRIC reg=([0-9a-f]+) value=(\d+)',log)}
        assert len(cycles)==metrics[0x38] and len(events)==metrics[0x40]
        assert all(row[0]==i for i,row in enumerate(cycles))
        assert ops[0][0]==0 and ops[-1][1]==len(cycles)
        data['variants'][key]=dict(manifest=pkg.manifest,descriptors=[list(struct.unpack_from('<16I',pkg.image,i*64)) for i in range(pkg.manifest['op_count'])],
            model_image=list(pkg.image),columns=columns,cycles=cycles,operators=ops,writes=len(events),commands=len(rec.rows))
        print(f'{key}: PASS {len(cycles)} cycles, {len(events)} checked writes, output={logical["output"]}')
    paths=sources+['software/host/npu_program.py','software/host/make_demo.py','software/host/nocpu_jtag.py',
        'hardware/fpga/fpga_top.sv','hardware/soc/sim/tb/tb_npu_program.sv',
        'docs/CPU_LESS_CONTROL.md','docs/NPU_DUAL_SRAM_IMPLEMENTATION_20261026.md',
        'docs/validation/npu_dual_sram_20261026.json']
    data['provenance']=dict(commit=run(['git','rev-parse','HEAD']).strip(),date='2026-10-27',
        source_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
        capture_sha256=hashlib.sha256((HERE/'capture.sv').read_bytes()).hexdigest(),
        sampling='pre-posedge; native control interface; behavioral synchronous SRAM; TB clock period 10 ns; cycles only',
        validation='Every materialized tensor write checked against independent logical reference by existing tb_npu_program; both output vectors checked.')
    evidence=json.loads((ROOT/'docs/validation/npu_dual_sram_20261026.json').read_text())
    data['recorded_evidence']={k:evidence[k] for k in ('date','architecture','scope')}
    data_js='window.NPU_DATA='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+';\n'
    (HERE/'data.js').write_text(data_js,encoding='utf-8')
    (HERE/'provenance.json').write_text(json.dumps(data['provenance'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    page=(HERE/'index.html').read_text(encoding='utf-8')
    page=page.replace('<link rel="stylesheet" href="style.css">','<style>\n'+(HERE/'style.css').read_text(encoding='utf-8')+'\n</style>')
    page=page.replace('<script src="data.js"></script><script src="app.js"></script>',
        '<script>\n'+data_js.replace('</script','<\\/script')+'\n</script><script>\n'+
        (HERE/'app.js').read_text(encoding='utf-8').replace('</script','<\\/script')+'\n</script>')
    (HERE/'standalone.html').write_text(page,encoding='utf-8')

if __name__=='__main__':main()
