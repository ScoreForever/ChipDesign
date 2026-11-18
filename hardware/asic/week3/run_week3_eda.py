#!/usr/bin/env python3
"""Serial, resumable Week 3 controller; stage receipts retain elapsed time and RSS."""
import argparse
from collections import Counter, defaultdict
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
OPT = HERE.parent/'optimize'
sys.path.insert(0,str(OPT))
from area_breakdown import lib_areas
from flow import libraries


def analyze_area(out, cand, shots):
    """Emit 11-11-compatible synthesis_cells.json and physical_area breakdown."""
    libs = libraries()
    ana = out/'week3_analysis'
    ana.mkdir(parents=True, exist_ok=True)
    areas, seq = lib_areas([libs['std_lib'], libs['macro_lib']])
    cells = re.findall(r'^\s*(\w+)\s+(\\?\S+)\s*\(', (out/'synthesis/mapped.v').read_text(errors='replace'), re.M)
    counts, area = defaultdict(int), defaultdict(float)
    seqcount, seqarea, latches = 0, 0.0, []
    for master, name in cells:
        if master not in areas:
            continue
        counts[master] += 1
        area[master] += areas[master]
        if master in seq:
            seqcount += 1
            seqarea += areas[master]
        if re.match(r'^(DLH|DLL|LAT|LHQ)', master):
            latches.append([master, name])
    syn_res = dict(
        mapped_leaf_count=sum(counts.values()),
        library_cell_area_um2=sum(area.values()),
        sequential_count=seqcount,
        sequential_area_um2=seqarea,
        latches=latches,
        master_counts=dict(counts),
        master_areas_um2=dict(area),
    )
    (ana/'synthesis_cells.json').write_text(json.dumps(syn_res, indent=2) + '\n')

    _, all_seq = lib_areas([libs['std_lib'], libs['io_lib'], libs['macro_lib']])
    active = {}
    with (cand/'final_cells.tsv').open() as f:
        for line in f:
            row = line.rstrip().split('\t')
            if len(row) >= 3:
                active[row[0].strip('{}')] = row[2]
    groups = defaultdict(lambda: dict(count=0, area_um2=0.0, sequential_count=0, sequential_area_um2=0.0))
    registers = defaultdict(lambda: dict(count=0, area_um2=0.0))
    masters, master_area = Counter(), defaultdict(float)
    classes = defaultdict(lambda: dict(count=0, area_um2=0.0))
    physical_categories = defaultdict(lambda: dict(count=0, area_um2=0.0))
    with (shots/'leaf_instances.tsv').open() as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    for row in rows:
        name, master = row['instance'], row['master']
        inst_area = (float(row['x1_um']) - float(row['x0_um'])) * (float(row['y1_um']) - float(row['y0_um']))
        kind = active.get(name)
        if kind is None:
            raise RuntimeError('instance missing from final statistics: ' + name)
        classes[kind]['count'] += 1
        classes[kind]['area_um2'] += inst_area
        masters[master] += 1
        master_area[master] += inst_area
        if kind == 'physical_or_io':
            if master.startswith('FILL'): category = 'core_filler'
            elif master.startswith('TAPCELL'): category = 'well_tap'
            elif master.startswith('DCAP'): category = 'decap'
            elif master.startswith('ANTENNA'): category = 'antenna_diode'
            elif master == 'PAD60LU_OBV': category = 'bond_opening'
            elif master.startswith('PFILLER'): category = 'io_filler'
            elif master == 'PCORNER': category = 'io_corner'
            elif row['base_class'] == 'pad': category = 'electrical_pad'
            else: category = 'other_physical_or_io'
            physical_categories[category]['count'] += 1
            physical_categories[category]['area_um2'] += inst_area
        if kind != 'active':
            continue
        if re.search(r'i_control[/_]i_npu', name): group = 'NPU'
        elif 'i_activation_i_store' in name or 'i_shared_store' in name: group = 'shared_store_logic'
        elif name.startswith('i_host'): group = 'host'
        elif name.startswith('i_control'): group = 'controller'
        else: group = 'backend_or_unattributed'
        d = groups[group]
        d['count'] += 1
        d['area_um2'] += inst_area
        if master in all_seq:
            d['sequential_count'] += 1
            d['sequential_area_um2'] += inst_area
            reg = re.sub(r'_reg(?:\[.*)?$', '', name)
            registers[reg]['count'] += 1
            registers[reg]['area_um2'] += inst_area
    for d in groups.values():
        d['combinational_area_um2'] = d['area_um2'] - d['sequential_area_um2']
    baseline = json.loads((cand/'prects_cells.json').read_text())
    final = json.loads((cand/'final_cells.json').read_text())
    if abs(classes['active']['area_um2'] - final['active_standard_area_um2']) > 0.01:
        raise RuntimeError('active area does not reconcile')
    if classes['active']['count'] != final['active_standard_instances']:
        raise RuntimeError('active count does not reconcile')
    phys_res = dict(
        classes=dict(classes),
        active_by_module=dict(groups),
        physical_categories=dict(physical_categories),
        sequential_count=sum(d['sequential_count'] for d in groups.values()),
        sequential_area_um2=sum(d['sequential_area_um2'] for d in groups.values()),
        cts_and_later_net_area_change_um2=final['active_standard_area_um2'] - baseline['active_standard_area_um2'],
        cts_and_later_net_instance_change=final['active_standard_instances'] - baseline['active_standard_instances'],
        note='DB instance boxes; active groups preserve hierarchy names, backend additions without a module prefix remain unattributed.',
        registers=dict(sorted(registers.items(), key=lambda x: -x[1]['area_um2'])),
        master_counts=dict(masters),
        master_area_um2=dict(master_area),
    )
    (ana/'physical_area_breakdown.json').write_text(json.dumps(phys_res, indent=2) + '\n')
    with (ana/'physical_area_modules.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['group', 'count', 'area_um2', 'sequential_count', 'sequential_area_um2', 'combinational_area_um2'])
        for group, d in groups.items():
            w.writerow([group, d['count'], d['area_um2'], d['sequential_count'], d['sequential_area_um2'], d['combinational_area_um2']])


def stage(root, name, command, cwd=None, env=None):
    """Resume completed stages only; preserve failed logs for diagnosis."""
    receipt = root/(name+'.json')
    if receipt.exists() and json.loads(receipt.read_text()).get('passed'):
        return
    started = time.monotonic()
    rss = root/(name+'.rss')
    with (root/(name+'.log')).open('w') as log:
        result = subprocess.run(['/usr/bin/time','-f','%M','-o',str(rss),*map(str,command)],
                                cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    record = dict(command=list(map(str,command)),elapsed_seconds=time.monotonic()-started,
                  peak_rss_kib=int(rss.read_text().splitlines()[-1]),passed=result.returncode==0)
    receipt.write_text(json.dumps(record,indent=2)+'\n')
    if not record['passed']:
        raise RuntimeError('Stage failed: '+name)


def main():
    """Execute the frozen synthesis/physical flow followed by external analyses."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out-dir',type=Path,required=True)
    ap.add_argument('--only-analysis',action='store_true')
    args = ap.parse_args()
    out = args.out_dir.resolve()
    cand = out/'candidates/diag70_h25'
    shots = out/'week3_shots'
    if args.only_analysis:
        analyze_area(out, cand, shots)
        return
    out.mkdir(parents=True,exist_ok=True)
    logs = out/'week3_stages'
    logs.mkdir(exist_ok=True)
    py = sys.executable
    base = [py,OPT/'flow.py']
    stage(logs,'preflight',base+['preflight','--output',out,'--clock-gating','--cols','8'])
    stage(logs,'synth',base+['synth','--output',out,'--clock-gating','--cols','8'])
    libs = libraries()
    stage(logs,'area_breakdown',[py,OPT/'area_breakdown.py','--netlist',out/'synthesis/mapped.v',
          '--lib',libs['std_lib'],'--lib',libs['macro_lib'],'--output',out/'area_breakdown.json'])
    stage(logs,'trial',base+['trial','--output',out,'--clock-gating','--cols','8','--name','diag70_h25',
          '--layout','diagonal_facing','--utilization','0.70','--halo','26','--congestion','--hold-target','0.25'])
    for name in ('postlayout','signoff'):
        stage(logs,name,[py,OPT/(name+'.py'),'--candidate',cand])
    shots.mkdir(exist_ok=True)
    if not (shots/'final.enc.dat').exists():
        shutil.copytree(cand/'final.enc.dat',shots/'final.enc.dat')
    env = dict(os.environ,SHOTS_DIR=str(shots))
    stage(logs,'screenshots',['xvfb-run','-a','-s','-screen 0 1800x1800x24','innovus','-files',HERE/'screenshots_week3.tcl'],shots,env)
    render = HERE.parents[2]/'docs/validation/eda_latest_20261111/reproduction/render_views.tcl'
    stage(logs,'render_views',['xvfb-run','-a','-s','-screen 0 1800x1800x24','innovus','-files',render],shots,env)
    stage(logs,'analysis',[py,HERE/'run_week3_eda.py','--out-dir',out,'--only-analysis'])
    stage(logs,'mmmc',[py,HERE/'run_mmmc_closure.py','--candidate',cand,'--out-dir',out/'week3_mmmc'])
    stage(logs,'power',[py,HERE/'measure_power.py','--candidate',cand,'--out-dir',out/'week3_power'])


if __name__=='__main__':
    main()
