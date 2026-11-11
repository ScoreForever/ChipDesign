import os
from pathlib import Path
import sys, subprocess, json, re
run=Path(os.environ['EDA_RUN'])
root=run/'src'
sys.path.insert(0,str(root/'hardware/asic/optimize'))
import flow
from area_breakdown import lib_areas
libs=flow.libraries();syn=run/'eda/synthesis';out=run/'analysis';out.mkdir(exist_ok=True)
subprocess.run(['python3',str(root/'hardware/asic/optimize/area_breakdown.py'),
    '--netlist',str(syn/'mapped.v'),'--lib',str(libs['std_lib']),'--lib',str(libs['macro_lib']),
    '--output',str(out/'synthesis_area_breakdown.json')],check=True)
areas,seq=lib_areas([libs['std_lib'],libs['macro_lib']])
cells=re.findall(r'^\s*(\w+)\s+(\\?\S+)\s*\(', (syn/'mapped.v').read_text(),re.M)
counts={};area={};seqcount=0;seqarea=0;latches=[]
for master,name in cells:
    if master not in areas:continue
    counts[master]=counts.get(master,0)+1
    area[master]=area.get(master,0)+areas[master]
    if master in seq:seqcount+=1;seqarea+=areas[master]
    if re.match(r'^(DLH|DLL|LAT|LHQ)',master):latches.append([master,name])
result=dict(mapped_leaf_count=sum(counts.values()),library_cell_area_um2=sum(area.values()),
            sequential_count=seqcount,sequential_area_um2=seqarea,latches=latches,master_counts=counts,
            master_areas_um2=area)
(out/'synthesis_cells.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if not k.startswith('master')},indent=2))
