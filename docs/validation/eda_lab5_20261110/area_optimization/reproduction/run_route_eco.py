from pathlib import Path
import hashlib,json,shutil,sys
root=Path.home()/'chip-fullstack-2026/ChipDesign_abi5_area129_icg_20261110_r2'
sys.path.insert(0,str(root/'source/hardware/asic/optimize'))
import flow
parent=root/'work/area129_icg/candidates/area129_icg_diag75'
out=parent.with_name('area129_icg_diag75_eco1')
out.mkdir(exist_ok=False)
shutil.copytree(parent/'driver',out/'driver')
for name in ('config.tcl','macro_pins.tsv'):
 shutil.copyfile(parent/name,out/name)
for name in ('repair_drc.tcl','route.tcl'):
 shutil.copyfile(root/name,out/'driver'/name)
pre=json.loads((parent/'prects_cells.json').read_text())
exp=json.loads((parent/'experiment.json').read_text())
exp.update(parent_candidate=str(parent),operation='bounded existing-marker signal DRC ECO from final checkpoint; no RTL, placement, CTS or timing exception changes',parent_checkpoint_sha256={str(p.relative_to(parent)):flow.digest(p) for p in [parent/'final.enc',*sorted((parent/'final.enc.dat').rglob('*'))] if p.is_file()})
flow.save(out/'experiment.json',exp)
route=(out/'driver/route.tcl').read_text()
finish=route[route.index('checkPlace placement.rpt'):]
(out/'driver/finish.tcl').write_text(finish)
script='source config.tcl\nsetMultiCpuUsage -localCpu $threads\nrestoreDesign {'+str(parent/'final.enc.dat')+'} my_soc_top\nsource config.tcl\n'
script+='set here [file dirname [file normalize [info script]]]\nsource $here/cell_stats.tcl\nsource $here/npu_jtag_cdc.tcl\n'
script+='set prects_stats [list '+str(pre['active_standard_instances'])+' '+str(pre['active_standard_area_um2'])+' 2 '+str(pre['macro_area_um2'])+']\n'
script+='set signal_ports {clk_i rst_ni tck_i tms_i td_i td_o irq_o}\nlassign [lindex [dbGet top.fPlan.box] 0] dx0 dy0 dx1 dy1\nset die_side_um [expr {$dx1-$dx0}]\n'
script+='source $here/repair_drc.tcl\nrepair_signal_drc\nsource $here/finish.tcl\n'
(out/'driver/eco.tcl').write_text(script)
flow.save(out/'driver/sha256.json',{str(p.relative_to(out/'driver')):flow.digest(p) for p in (out/'driver').rglob('*') if p.is_file() and p.name!='sha256.json'})
state=flow.launch(['innovus','-no_gui','-files',str(out/'driver/eco.tcl')],out,'PNR_OPT_PHYSICAL_COMPLETE')
result=flow.finalize_candidate(out,state)
print(json.dumps(result,indent=2),flush=True)
if not result.get('eligible'):raise RuntimeError('ECO physical acceptance failed')
