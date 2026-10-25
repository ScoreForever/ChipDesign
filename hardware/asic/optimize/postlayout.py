#!/usr/bin/env python3
"""Strict gate/SDF physical-JTAG acceptance; run on the course Linux node."""
from pathlib import Path
import argparse
import gzip
import json
import re
import subprocess
import time
from flow import HERE, ROOT, libraries, launch, recover_tool, metrics, save, digest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--candidate',type=Path,required=True)
    args=ap.parse_args();candidate=args.candidate.resolve();out=candidate/'sdf'
    if not metrics(candidate).get('eligible'):raise RuntimeError('candidate has not passed physical acceptance')
    artifacts=json.loads((candidate/'artifact_sha256.json').read_text())
    if not artifacts or any(digest(candidate/p)!=sha for p,sha in artifacts.items()):
        raise RuntimeError('physical artifacts changed since acceptance')
    if out.exists() and not (out/'status.json').exists():
        # No tool can have started before launch writes its status. Preserve
        # partially generated inputs, then start a new, reviewable attempt.
        out.rename(candidate/('sdf_incomplete_'+str(time.time_ns())))
    libs=libraries();testbench=candidate/'driver/tb_fullchip.sv'
    if not out.exists():
        out.mkdir()
        with gzip.open(candidate/'outputs/routed.sdf.gz','rt') as source:
            (out/'routed.sdf').write_text(source.read())
        subprocess.run(['python3',str(HERE/'make_trace.py'),str(out/'commands.txt')],check=True,cwd=ROOT)
        save(out/'inputs_sha256.json',{str(p):digest(p) for p in [candidate/'outputs/simulation.v',out/'routed.sdf',out/'commands.txt',testbench]})
        command=['xrun','-64bit','-sv','-timescale','1ns/1ps','-access','+rwc','-top','tb_fullchip',
          str(libs['std_verilog']),str(libs['io_verilog']),str(libs['macro_verilog']),
          str(candidate/'outputs/simulation.v'),str(testbench),
          '+SDF='+str(out/'routed.sdf'),'+TRACE='+str(out/'commands.txt'),'-l','simulation.log']
        state=launch(command,out,'PNR_OPT_JTAG_PASS',timeout=14400)
    else:
        saved=json.loads((out/'inputs_sha256.json').read_text())
        if any(digest(p)!=sha for p,sha in saved.items()):raise RuntimeError('SDF inputs changed')
        state=recover_tool(out,'PNR_OPT_JTAG_PASS')
    logs='\n'.join(p.read_text(errors='replace') for p in out.glob('*.log'))
    timing_fail=bool(re.search(r'(?i)timing violation|timing check violation|notifier|SDFNEP|SDFNET|SDFINF|SDFCTE|SDFRDE|\*E,|\*F,',logs))
    annotation=out/'sdf_annotation.log'
    annotated=annotation.is_file() and bool(re.search(r'(?i)annotat',annotation.read_text(errors='replace')))
    result=dict(passed=state['status']=='complete' and not timing_fail and annotated,
       timing_diagnostics=timing_fail,annotation_log_present=annotation.is_file(),
       sources_sha256=json.loads((out/'inputs_sha256.json').read_text()))
    save(out/'result.json',result)
    if not result['passed']:raise RuntimeError('SDF acceptance failed; retained all evidence')

if __name__=='__main__':main()
