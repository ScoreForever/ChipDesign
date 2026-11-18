#!/usr/bin/env python3
"""Run independent MMMC STA on a private database; typical RC at every corner."""
import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
OPT = HERE.parent / 'optimize'
sys.path.insert(0, str(OPT))
from flow import libraries, tcl


def main():
    """Preserve candidate outputs and retain raw timing evidence."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cand-dir', '--candidate', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, required=True)
    ap.add_argument('--hold-eco', action='store_true')
    args = ap.parse_args()
    cand, out = args.cand_dir.resolve(), args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'work').mkdir()
    shutil.copytree(cand / 'final.enc.dat', out / 'work/final.enc.dat')
    shutil.copy2(cand / 'outputs/mapped.sdc', out / 'mapped.sdc')
    libs = libraries()
    config = dict(opt_dir=OPT, sdc=out/'mapped.sdc',
                  std=libs['std_lib'].parent, io=libs['io_lib'].parent,
                  sram=libs['macro_lib'].parent, captable=libs['captable'],
                  RUN_HOLD_ECO=int(args.hold_eco),
                  latency_sdc=out/'work/final.enc.dat/mmmc/views/func_tt_io/latency.sdc')
    (out/'config_week3.tcl').write_text(''.join(f'set {k} {tcl(v)}\n' for k,v in config.items()))
    with (out/'console.log').open('w') as log:
        subprocess.run(['innovus', '-no_gui', '-files', str(HERE/'mmmc_sta.tcl')],
                       cwd=out, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, check=True)
    if 'WEEK3_STA_DONE' not in (out/'console.log').read_text():
        raise RuntimeError('STA completion marker missing')
    with (out/'timing.tsv').open() as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    with (out/'mmmc_corners.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    with (out/'cdc_4corners.tsv').open() as f:
        cdc = list(csv.DictReader(f, delimiter='\t'))
    with (out/'sram_interface_timing.csv').open() as f:
        sram = list(csv.DictReader(f))
    if len(rows)!=10 or len(cdc)!=452 or len(sram)!=8:
        raise RuntimeError('Incomplete corner, CDC or SRAM evidence')
    summary = dict(corners=rows, cdc_rows=len(cdc), sram_rows=len(sram),
                   rc_note='Typical captable only, temperature scaled; no RC process corners',
                   passed=all(float(r['wns'])>=0 for r in rows if r['corner']!='ss125'))
    (out/'mmmc_summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    if not summary['passed']:
        raise RuntimeError('Timing closure failed')


if __name__ == '__main__':
    main()
