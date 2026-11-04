"""Replay every published zoo golden frame and selected configurations on RTL."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'software/host'),str(ROOT/'hardware/npu/scripts')]
import run_program_regression as R
from npu_program import compile_model, reference


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--jobs',type=int,default=4)
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    models=ROOT/'software/zoo/pretrained'
    manifest=json.loads((models/'MANIFEST.json').read_text())
    report=dict(passed=False,tests=[],host_rejections=[],package_hashes={},golden_frames=0)
    jobs=[]
    for name,entry in manifest.items():
        run=models/name
        for file,expected in entry['sha256'].items():
            # Git blob bytes avoid checkout CRLF conversion of text exports.
            path=(run/file).relative_to(ROOT).as_posix()
            blob=subprocess.check_output(['git','show','origin/feature/int4-v2:'+path],cwd=ROOT)
            digest=hashlib.sha256(blob).hexdigest()
            if digest!=expected:raise AssertionError('published hash mismatch: '+path)
            report['package_hashes'][path]=digest
        model=json.loads((run/'model.json').read_text())
        golden=json.loads((run/'golden.json').read_text())
        assert len(golden['frames'])==16
        for cols in (8,4):
            pkg=compile_model(model,cols=cols)
            assert pkg.image==(run/f'c{cols}/model.bin').read_bytes(),name
        for index,frame in enumerate(golden['frames']):
            result=reference(model,frame['input'])[model['nodes'][-1]['output']]
            assert list(result)==frame['output'],(name,index)
            assert bytes(R.encoded(result,golden['output_dtype'])).hex()==frame['output_hex']
            assert bytes(R.encoded(frame['input'],golden['input_dtype'])).hex()==frame['input_hex']
            jobs.append((name,index,model,frame,8,True,False,'all'))
            report['golden_frames']+=1
        first=golden['frames'][0]
        jobs.extend([(name,0,model,first,4,True,False,'all'),
                     (name,0,model,first,8,True,True,'all')])
        for label,fuse in [('none',False),('no_requant',['relu','maxpool']),
                           ('no_relu',['requant','maxpool']),('no_maxpool',['requant','relu'])]:
            try:compile_model(model,cols=8,fuse=fuse)
            except ValueError as exc:
                report['host_rejections'].append(dict(model=name,configuration=label,reason=str(exc)))
            else:jobs.append((name,0,model,first,8,fuse,False,label))
    sources=(ROOT/'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
    report['sources_sha256']={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources}
    report['model_commit']=subprocess.check_output(['git','rev-parse','origin/feature/int4-v2'],cwd=ROOT,text=True).strip()
    images={}
    for cols,jtag in [(8,False),(4,False),(8,True)]:
        image=out/f'program_{cols}_{jtag}.vvp'
        defines=(['-DCOLS4','-DNPU_ARRAY_COLS=4'] if cols==4 else [])+(['-DJTAG_TEST'] if jtag else [])
        R.run(['iverilog','-g2012','-s','tb_npu_program','-o',image,*defines,*sources,
               'hardware/soc/sim/tb/tb_npu_program.sv'],out/f'compile_{cols}_{jtag}.log')
        images[cols,jtag]=image
    def one(job):
        name,index,model,frame,cols,fuse,jtag,label=job
        row=R.simulate(out,images[cols,jtag],f'{name}_frame{index:02d}_{label}',model,frame['input'],cols,fuse,jtag)
        row.update(model=name,frame=index,cols=cols,jtag=jtag,fusion=label,label=frame['label'],predicted=frame['predicted'])
        return row
    try:
        with ThreadPoolExecutor(args.jobs) as pool:
            for future in as_completed([pool.submit(one,job) for job in jobs]):
                row=future.result();report['tests'].append(row)
                print('PASS',row['case'],row['cycles'],flush=True)
        assert len(report['tests'])==len(jobs) and report['golden_frames']==112
        report['tests'].sort(key=lambda row:row['case'])
        report['passed']=True
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print('ALL',len(report['tests']),'ZOO RTL TESTS PASSED; host capacity rejections:',len(report['host_rejections']))


if __name__=='__main__':main()
