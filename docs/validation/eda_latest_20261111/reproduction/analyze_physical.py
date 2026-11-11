import os
from pathlib import Path
from collections import defaultdict,Counter
import json,csv,sys,re
RUN=Path(os.environ['EDA_RUN'])
sys.path.insert(0,str(RUN/'src/hardware/asic/optimize'))
from flow import libraries
from area_breakdown import lib_areas
C=Path((RUN/'selected_candidate.path').read_text().strip())
lib=libraries();_,seq=lib_areas([lib['std_lib'],lib['io_lib'],lib['macro_lib']])
active={}
with (C/'final_cells.tsv').open() as f:
    for line in f:
        row=line.rstrip().split('\t')
        active[row[0].strip('{}')]=row[2]
groups=defaultdict(lambda:dict(count=0,area_um2=0.,sequential_count=0,sequential_area_um2=0.))
registers=defaultdict(lambda:dict(count=0,area_um2=0.))
masters=Counter(); master_area=defaultdict(float)
classes=defaultdict(lambda:dict(count=0,area_um2=0.))
physical_categories=defaultdict(lambda:dict(count=0,area_um2=0.))
with (C/'leaf_instances.tsv').open() as f:
    rows=list(csv.DictReader(f,delimiter='\t'))
for row in rows:
    name=row['instance'];master=row['master']
    area=(float(row['x1_um'])-float(row['x0_um']))*(float(row['y1_um'])-float(row['y0_um']))
    kind=active.get(name)
    if kind is None:raise RuntimeError('instance missing from final statistics: '+name)
    classes[kind]['count']+=1;classes[kind]['area_um2']+=area
    masters[master]+=1;master_area[master]+=area
    if kind=='physical_or_io':
        if master.startswith('FILL'):category='core_filler'
        elif master.startswith('TAPCELL'):category='well_tap'
        elif master.startswith('DCAP'):category='decap'
        elif master.startswith('ANTENNA'):category='antenna_diode'
        elif master=='PAD60LU_OBV':category='bond_opening'
        elif master.startswith('PFILLER'):category='io_filler'
        elif master=='PCORNER':category='io_corner'
        elif row['base_class']=='pad':category='electrical_pad'
        else:category='other_physical_or_io'
        physical_categories[category]['count']+=1;physical_categories[category]['area_um2']+=area
    if kind!='active':continue
    if re.search(r'i_control[/_]i_npu',name):group='NPU'
    elif 'i_activation_i_store' in name:group='shared_store_logic'
    elif name.startswith('i_host'):group='host'
    elif name.startswith('i_control'):group='controller'
    else:group='backend_or_unattributed'
    d=groups[group];d['count']+=1;d['area_um2']+=area
    if master in seq:
        d['sequential_count']+=1;d['sequential_area_um2']+=area
        reg=re.sub(r'_reg(?:\[.*)?$', '',name)
        registers[reg]['count']+=1;registers[reg]['area_um2']+=area
for d in groups.values():d['combinational_area_um2']=d['area_um2']-d['sequential_area_um2']
baseline=json.loads((C/'prects_cells.json').read_text());final=json.loads((C/'final_cells.json').read_text())
if abs(classes['active']['area_um2']-final['active_standard_area_um2'])>0.01:raise RuntimeError('active area does not reconcile')
if classes['active']['count']!=final['active_standard_instances']:raise RuntimeError('active count does not reconcile')
result=dict(classes=dict(classes),active_by_module=dict(groups),
    physical_categories=dict(physical_categories),
    sequential_count=sum(d['sequential_count'] for d in groups.values()),
    sequential_area_um2=sum(d['sequential_area_um2'] for d in groups.values()),
    cts_and_later_net_area_change_um2=final['active_standard_area_um2']-baseline['active_standard_area_um2'],
    cts_and_later_net_instance_change=final['active_standard_instances']-baseline['active_standard_instances'],
    note='DB instance boxes; active groups preserve hierarchy names, backend additions without a module prefix remain unattributed. CKBD master counts alone do not prove exclusive CTS use.',
    registers=dict(sorted(registers.items(),key=lambda x:-x[1]['area_um2'])),
    master_counts=dict(masters),master_area_um2=dict(master_area))
(RUN/'analysis/physical_area_breakdown.json').write_text(json.dumps(result,indent=2)+'\n')
with (RUN/'analysis/physical_area_modules.csv').open('w',newline='') as f:
    w=csv.writer(f);w.writerow(['group','count','area_um2','sequential_count','sequential_area_um2','combinational_area_um2'])
    for group,d in groups.items():w.writerow([group,d['count'],d['area_um2'],d['sequential_count'],d['sequential_area_um2'],d['combinational_area_um2']])
print(json.dumps({k:v for k,v in result.items() if k not in ('registers','master_counts','master_area_um2')},indent=2))
