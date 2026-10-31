#!/usr/bin/env python3
"""Build the CPU-less EGo1 system using an ASCII temporary staging directory.

Vivado IP generation on Windows cannot reliably handle this repository's Unicode
path. Only the selected sources/constraints/scripts are copied; outputs and exact
source hashes are copied back to a NEW build/ directory. Staging is preserved for
diagnosis. Default is synthesis only; --implement also places/routes/creates a
bitstream, but never programs a board.
"""
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
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--vivado-root',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--jobs',type=int,default=4)
    ap.add_argument('--mhz',type=float,default=50)
    ap.add_argument('--cols',type=int,choices=(4,8),default=8)
    ap.add_argument('--implement',action='store_true')
    args=ap.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(ROOT/'build') or out.exists():
        ap.error('--output must be a new directory inside build/')
    if args.jobs<1 or not 1<=args.mhz<=500:ap.error('invalid jobs/frequency')
    vivado=args.vivado_root/'bin'/('vivado.bat' if os.name=='nt' else 'vivado')
    if not vivado.is_file():ap.error(f'Vivado not found: {vivado}')
    stage=Path(tempfile.mkdtemp(prefix='chipdesign_nocpu_'))
    if not str(stage).isascii():ap.error('TEMP directory must have an ASCII path for Vivado')
    repo=stage/'source';run=stage/'run';run.mkdir()
    filelist=Path('hardware/soc/sim/filelists/chipdesign_nocpu.f')
    sources=(ROOT/filelist).read_text().splitlines()
    sources += [filelist.as_posix(),'hardware/fpga/fpga_top.sv',
                'hardware/fpga/ego1_pins.xdc','hardware/fpga/ego1_timing.xdc',
                'hardware/fpga/ego1_configuration.xdc','hardware/fpga/check_bram_drc.tcl',
                'hardware/fpga/scripts/build_ego1.tcl',
                'hardware/fpga/scripts/constrain_npu_jtag_cdc.tcl',
                'hardware/fpga/scripts/audit_jtag_io.tcl']
    hashes={}
    for source in sources:
        src=ROOT/source;dest=repo/source;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dest);hashes[source]=hashlib.sha256(src.read_bytes()).hexdigest()
    env=os.environ.copy();env['XILINX_LOCAL_USER_DATA']='no'
    tmp=stage/'tmp';tmp.mkdir();env['TEMP']=env['TMP']=str(tmp)
    command=[str(vivado),'-mode','batch','-source',str(repo/'hardware/fpga/scripts/build_ego1.tcl'),
             '-log',str(run/'vivado.log'),'-journal',str(run/'vivado.jou'),
             '-tclargs',str(repo),str(run),str(args.jobs),str(args.mhz),
             'full' if args.implement else 'synth_only',str(args.cols)]
    startup=None
    if os.name=='nt':
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow=subprocess.SW_HIDE
    print(f'Vivado staging: {stage}',flush=True)
    try:
        with (run/'console.log').open('w') as log:
            result=subprocess.run(command,cwd=run,env=env,startupinfo=startup,stdout=log,stderr=subprocess.STDOUT)
        code=result.returncode
    finally:
        shutil.copytree(run,out)
        (out/'provenance.json').write_text(json.dumps({'staging':str(stage),'command':command,
            'source_sha256':hashes,'synthesis_only':not args.implement},indent=2)+'\n')
    summary=out/'summary.md'
    if code or not summary.is_file():
        print((out/'console.log').read_text(errors='replace')[-5000:])
        raise SystemExit(code or 1)
    print(summary.read_text(),flush=True)
    print(f'Build evidence: {out}',flush=True)


if __name__=='__main__':main()
