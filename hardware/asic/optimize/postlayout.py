#!/usr/bin/env python3
"""Strict gate/SDF physical-JTAG acceptance; run on the course Linux node."""
from pathlib import Path
import argparse
import gzip
import json
import re
import subprocess
from flow import HERE, ROOT, libraries, launch, save, digest

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--candidate',type=Path,required=True)
    args=ap.parse_args();candidate=args.candidate.resolve();out=candidate/'sdf'
    if out.exists():raise RuntimeError('refusing to overwrite SDF evidence')
    out.mkdir()
    libs=libraries()
    with gzip.open(candidate/'outputs/routed.sdf.gz','rt') as source:
        (out/'routed.sdf').write_text(source.read())
    subprocess.run(['python3',str(HERE/'make_trace.py'),str(out/'commands.txt')],check=True,cwd=ROOT)
    command=['xrun','-64bit','-sv','-timescale','1ns/1ps','-access','+rwc','-top','tb_fullchip',
      str(libs['std_verilog']),str(libs['io_verilog']),str(libs['macro_verilog']),
      str(candidate/'outputs/simulation.v'),str(HERE/'tb_fullchip.sv'),
      '+SDF='+str(out/'routed.sdf'),'+TRACE='+str(out/'commands.txt'),'-l','simulation.log']
    state=launch(command,out,'PNR_OPT_JTAG_PASS',timeout=14400)
    logs='\n'.join(p.read_text(errors='replace') for p in out.glob('*.log'))
    timing_fail=bool(re.search(r'(?i)timing violation|timing check violation|notifier|SDFNEP|SDFNET|SDFINF|SDFCTE|SDFRDE|\*E,|\*F,',logs))
    annotation=(out/'sdf_annotation.log')
    annotated=annotation.is_file() and bool(re.search(r'(?i)annotat',annotation.read_text(errors='replace')))
    result=dict(passed=state['status']=='complete' and not timing_fail and annotated,
       timing_diagnostics=timing_fail,annotation_log_present=annotation.is_file(),
       sources_sha256={str(p):digest(p) for p in [candidate/'outputs/simulation.v',out/'routed.sdf',out/'commands.txt',HERE/'tb_fullchip.sv']})
    save(out/'result.json',result)
    if not result['passed']:raise RuntimeError('SDF acceptance failed; retained all evidence')
if __name__=='__main__':main()
