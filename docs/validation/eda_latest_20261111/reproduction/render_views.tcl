# Read-only exports from the completed physical database.
restoreDesign final.enc.dat my_soc_top
file mkdir images
catch {gui_show}
fit
dumpPictures -dir images -prefix abi5
file mkdir images/fullscreen
if {[catch {dumpPictures -dir images/fullscreen -prefix abi5 -fullScreen} msg]} {puts "FULLSCREEN_VIEW_SKIPPED: $msg"}
dumpToGIF images/abi5_final.gif
catch {report_ccopt_clock_trees -file clock_trees.rpt}
catch {report_ccopt_skew_groups -file clock_skew_groups.rpt}
set fp [open database_geometry.tsv w]
puts $fp "die\t[dbGet top.fPlan.box]"
puts $fp "core\t[dbGet top.fPlan.coreBox]"
close $fp
set fp [open leaf_instances.tsv w]
puts $fp "instance\tmaster\tbase_class\tx0_um\ty0_um\tx1_um\ty1_um"
foreach inst [dbGet top.insts] {
    puts $fp "[lindex [dbGet $inst.name] 0]\t[dbGet $inst.cell.name]\t[dbGet $inst.cell.baseClass]\t[join [lindex [dbGet $inst.box] 0] \t]"
}
close $fp
puts "ABI5_FINAL_VIEWS_EXPORTED"
exit
