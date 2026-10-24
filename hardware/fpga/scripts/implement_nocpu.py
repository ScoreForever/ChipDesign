"""Place/route an existing source-verified NPU checkpoint, without a bitstream."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vivado-root',type=Path,required=True)
    p.add_argument('--synthesis-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();source=args.synthesis_dir.resolve();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'build') or out.exists():p.error('output must be a new directory inside build/')
    provenance=json.loads((source/'provenance.json').read_text())
    for name,expected in provenance['source_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
            p.error(f'checkpoint sources no longer match: {name}; rebuild synthesis')
    stage=Path(tempfile.mkdtemp(prefix='npu_route_'));run=stage/'run';run.mkdir()
    checkpoint=stage/'synth.dcp';shutil.copy2(source/'synth.dcp',checkpoint)
    script=stage/'implement.tcl';original=ROOT/'hardware/fpga/scripts/implement_nocpu.tcl';shutil.copy2(original,script)
    vivado=args.vivado_root/'bin'/('vivado.bat' if os.name=='nt' else 'vivado')
    command=[str(vivado),'-mode','batch','-source',str(script),'-log',str(run/'vivado.log'),
             '-journal',str(run/'vivado.jou'),'-tclargs',str(checkpoint),str(run)]
    env=os.environ.copy();tmp=stage/'tmp';tmp.mkdir();env['TEMP']=env['TMP']=str(tmp)
    env['XILINX_LOCAL_USER_DATA']='no'
    startup=None
    if os.name=='nt':
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=subprocess.SW_HIDE
    print('Implementation staging:',stage,flush=True)
    try:
        with (run/'console.log').open('w') as log:
            result=subprocess.run(command,cwd=run,env=env,startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
    finally:
        shutil.copytree(run,out)
        (out/'provenance.json').write_text(json.dumps(dict(synthesis_dir=str(source),
             source_sha256=provenance['source_sha256'],checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
             implementation_script_sha256=hashlib.sha256(original.read_bytes()).hexdigest()),indent=2)+'\n')
    if result.returncode or not (out/'metrics.tsv').exists():
        print((out/'console.log').read_text(errors='replace')[-4000:]);raise SystemExit(result.returncode or 1)
    print((out/'metrics.tsv').read_text())
    metrics=dict(line.split('\t',1) for line in (out/'metrics.tsv').read_text().splitlines())
    if metrics['result']!='PASS':raise SystemExit(1)
if __name__=='__main__':main()
