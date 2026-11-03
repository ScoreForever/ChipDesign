#!/usr/bin/env python3
"""Open-source XC7 resource estimate (yosys synth_xilinx) for FPGA headroom.

Not a Vivado result: yosys maps differently, so only compare estimates with
each other. Calibrate against a revision whose Vivado numbers are known (the
2026-11-01 board-validated INT4 build: 17,255 LUT / 10,347 FF / 8 BRAM36).

  python hardware/fpga/scripts/estimate_xc7.py --output build/xc7 [--define NPU_ENABLE_OP_TRACE=0 ...]
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--cols', type=int, default=8)
    ap.add_argument('--define', action='append', default=[])
    ap.add_argument('--root', type=Path, default=ROOT, help='source tree (for older revisions)')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = args.root.resolve()
    files = [str(root / l.strip()) for l in (root / 'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
             if l.strip() and not l.startswith('+')]
    defines = ['SYNTHESIS', 'CHIPDESIGN_FPGA_MEM', f'NPU_ARRAY_COLS={args.cols}', *args.define]
    dflags = ' '.join('-D' + d for d in defines)
    script = (f"read_verilog -sv {dflags} {' '.join(files)}\n"
              "synth_xilinx -family xc7 -top my_soc_top -flatten\n"
              f"tee -o {out}/stat.txt stat\n")
    (out / 'run.ys').write_text(script)
    log = subprocess.run(['yosys', '-q', '-l', str(out / 'yosys.log'), '-s', str(out / 'run.ys')],
                         text=True, capture_output=True)
    if log.returncode:
        raise SystemExit(log.stdout[-3000:] + log.stderr[-3000:])
    stat = (out / 'stat.txt').read_text()
    # "<count>   <CELLTYPE>" rows of the yosys 0.6x statistics table.
    cells = {m[2]: int(m[1]) for m in re.finditer(r'^\s+(\d+)\s+([A-Z][A-Z0-9_]*)\s*$', stat, re.M)}
    luts = sum(v for k, v in cells.items() if re.fullmatch(r'LUT[1-6]', k))
    ffs = sum(v for k, v in cells.items() if re.fullmatch(r'FD[CPRS]E(_1)?', k))
    lutram = sum(v for k, v in cells.items() if k.startswith('RAM') and not k.startswith('RAMB'))
    bram = cells.get('RAMB36E1', 0) + cells.get('RAMB18E1', 0) / 2
    result = dict(defines=defines, lut=luts, ff=ffs, lutram_cells=lutram, bram36=bram,
                  dsp=cells.get('DSP48E1', 0), carry4=cells.get('CARRY4', 0), cells=cells)
    (out / 'estimate.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'cells'}))


if __name__ == '__main__':
    main()
