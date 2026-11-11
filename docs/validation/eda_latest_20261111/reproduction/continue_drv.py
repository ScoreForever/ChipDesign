import os
"""Continue a real routed checkpoint to fix transition DRVs; retain its predecessor."""
from pathlib import Path
import json,shutil,sys,subprocess
R=Path(os.environ['EDA_RUN'])
sys.path.insert(0,str(R/'src/hardware/asic/optimize'))
from flow import launch,finalize_candidate,save,digest,summarize,metrics,validate_reports
O=R/'eda'
P=O/'candidates/latest_diag75'
C=O/'candidates/latest_diag75_drvfix'
parent=json.loads((P/'result.json').read_text())
save(P/'result_before_si_drv_audit.json',parent)
save(P/'checks_before_si_drv_audit.json',json.loads((P/'checks.json').read_text()))
save(P/'checks.json',validate_reports(P))
parent=metrics(P);save(P/'result.json',parent)
def select(c):
    save(R/'selected_candidate.json',dict(directory=str(c),result=json.loads((c/'result.json').read_text())))
    (R/'selected_candidate.path').write_text(str(c)+'\n')
if json.loads((P/'status.json').read_text()).get('status')!='complete':raise RuntimeError('parent physical tool did not finish')
for path,sha in json.loads((P/'artifact_sha256.json').read_text()).items():
    if digest(P/path)!=sha:raise RuntimeError('parent export changed: '+path)
if C.exists():raise RuntimeError('preserve the previous ECO attempt: '+str(C))
C.mkdir()
for name in ('config.tcl','macro_pins.tsv','prects_cells.tsv','prects_cells.json'):
    shutil.copy2(P/name,C/name)
shutil.copytree(P/'driver',C/'driver')
exp=json.loads((P/'experiment.json').read_text())
exp.update(continuation_from=str(P),continuation_kind='postRoute DRV optimization from routed.enc.dat')
exp['validation_flow_sha256']=digest(R/'src/hardware/asic/optimize/flow.py')
save(C/'experiment.json',exp)
db_hashes={str(p.relative_to(P)):digest(p) for p in (P/'routed.enc.dat').rglob('*') if p.is_file()}
save(C/'continuation_inputs.json',dict(parent_result=parent,parent_artifacts=json.loads((P/'artifact_sha256.json').read_text()),routed_database_sha256=db_hashes))
pre=json.loads((P/'prects_cells.json').read_text())
stats=[pre['active_standard_instances'],pre['active_standard_area_um2'],pre['macro_count'],pre['macro_area_um2']]
route=(P/'driver/route.tcl').read_text()
tail=route.split('saveDesign routed.enc\n',1)[1]
(C/'finish_exports.tcl').write_text(tail)
script=f'''restoreDesign {{{P}/routed.enc.dat}} my_soc_top
source {{{C}/config.tcl}}
set here {{{C}/driver}}
setMultiCpuUsage -localCpu $threads
source $here/cell_stats.tcl
source $here/npu_jtag_cdc.tcl
set prects_stats {{{' '.join(map(str,stats))}}}
lassign [lindex [dbGet top.fPlan.box] 0] dx0 dy0 dx1 dy1
set die_side_um [expr {{$dx1-$dx0}}]
set signal_ports {{clk_i rst_ni tck_i tms_i td_i td_o irq_o}}
optDesign -postRoute -drv -outDir postroute_drv_repair
saveDesign routed.enc
source {{{C}/finish_exports.tcl}}
'''
(C/'eco_continue.tcl').write_text(script)
save(C/'continuation_script_sha256.json',{p.name:digest(p) for p in (C/'eco_continue.tcl',C/'finish_exports.tcl')})
state=launch(['innovus','-no_gui','-files',str(C/'eco_continue.tcl')],C,'PNR_OPT_PHYSICAL_COMPLETE',timeout=14400)
result=finalize_candidate(C,state)
if any(digest(P/p)!=sha for p,sha in db_hashes.items()):raise RuntimeError('parent routed database changed')
summarize(O)
print(json.dumps(result,indent=2))
if not result.get('eligible'):raise RuntimeError('DRV continuation did not pass physical acceptance; evidence retained')
select(C)
print('DRV_CONTINUATION_COMPLETE')
