#!/usr/bin/env python3
"""Register-level area breakdown of a Genus mapped netlist.

Flip-flops keep their RTL register names (e.g. ...psum_reg[3][17]), so their
area is grouped by register; everything else in a module is combinational.
Cell areas come from the Liberty file.

  python area_breakdown.py --netlist mapped.v --lib std.lib [--lib macro.lib] --output area_breakdown.json
"""
import argparse
import collections
import json
import re
from pathlib import Path


def lib_areas(paths):
    areas, seq = {}, set()
    for path in paths:
        text = Path(path).read_text(errors='replace')
        for m in re.finditer(r'\bcell\s*\(\s*"?([\w]+)"?\s*\)\s*\{', text):
            body = text[m.end():m.end() + 6000]
            a = re.search(r'\barea\s*:\s*([\d.]+)', body)
            if a:
                areas[m[1]] = float(a[1])
            if re.search(r'\b(ff|latch)\s*\(', body.split('cell (')[0] if 'cell (' in body else body):
                seq.add(m[1])
    return areas, seq


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--netlist', type=Path, required=True)
    ap.add_argument('--lib', type=Path, action='append', required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    areas, seq = lib_areas(args.lib)
    text = args.netlist.read_text(errors='replace')
    modules = {}
    for m in re.finditer(r'(?ms)^module\s+(\w+)(.*?)^endmodule', text):
        modules[m[1]] = m[2]
    groups = collections.defaultdict(lambda: [0, 0.0])
    comb = collections.defaultdict(float)
    macros = 0.0
    for module, body in modules.items():
        for cell, inst in re.findall(r'^\s*(\w+)\s+(\\?\S+)\s*\(', body, re.M):
            if cell not in areas:
                continue
            if cell.startswith('RA1SHD'):
                macros += areas[cell]
                continue
            if cell in seq:
                name = inst.lstrip('\\')
                reg = re.sub(r'(_reg)?(\[\d+\])+$', '', re.sub(r'_reg\[.*$', '', name))
                reg = re.sub(r'\d+$', '', reg) if not reg.endswith('_reg') else reg[:-4]
                groups[f'{module}:{reg}'][0] += 1
                groups[f'{module}:{reg}'][1] += areas[cell]
            else:
                comb[module] += areas[cell]
    ff_total = sum(a for _, a in groups.values())
    result = dict(macro_area_um2=macros, flop_area_um2=ff_total, comb_area_um2=sum(comb.values()),
                  comb_by_module=dict(sorted(comb.items(), key=lambda kv: -kv[1])),
                  flops_by_register={k: dict(count=c, area_um2=round(a, 1))
                                     for k, (c, a) in sorted(groups.items(), key=lambda kv: -kv[1][1])})
    args.output.write_text(json.dumps(result, indent=1) + '\n')
    print(f"macros {macros/1e6:.3f} mm2, flops {ff_total/1e6:.3f} mm2, comb {result['comb_area_um2']/1e6:.3f} mm2")
    for k, v in list(result['flops_by_register'].items())[:30]:
        print(f"  {v['area_um2']/1e3:8.1f}k um2 {v['count']:6d} FF  {k}")
    for k, v in list(result['comb_by_module'].items())[:10]:
        print(f"  comb {v/1e3:8.1f}k um2  {k}")


if __name__ == '__main__':
    main()
