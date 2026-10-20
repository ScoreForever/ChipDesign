restoreDesign ../my_soc_top/backup/my_soc_top_postPinLabels.enc.dat my_soc_top

set out_dir ../my_soc_top/reports/reusable_audit
file mkdir $out_dir

set summary [open $out_dir/db_summary.txt w]
puts $summary "DIE_BOX [dbGet top.fPlan.box]"
puts $summary "CORE_BOX [dbGet top.fPlan.coreBox]"
puts $summary "INST_COUNT [llength [dbGet top.insts]]"
puts $summary "STD_INST_COUNT [llength [dbGet -p2 top.insts.cell.subClass core*]]"
puts $summary "BLOCK_INST_NAMES [dbGet [dbGet -p2 top.insts.cell.subClass block*].name]"
puts $summary "BLOCK_CELL_NAMES [dbGet [dbGet -p2 top.insts.cell.subClass block*].cell.name]"
puts $summary "BLOCK_BOXES [dbGet [dbGet -p2 top.insts.cell.subClass block*].box]"
close $summary

redirect $out_dir/report_area.rpt {report_area}
redirect $out_dir/report_gate_count.rpt {reportGateCount}

set lf [open $out_dir/npu_leaf_area.csv w]
puts $lf "name,cell,area_um2"
foreach inst [dbGet top.insts] {
    set name [dbGet $inst.name]
    if {![string match "i_control/i_npu/*" $name]} {continue}
    set cell [dbGet $inst.cell.name]
    set box [lindex [dbGet $inst.box] 0]
    if {[llength $box] != 4} {continue}
    set area [expr {([lindex $box 2]-[lindex $box 0])*([lindex $box 3]-[lindex $box 1])}]
    puts $lf "$name,$cell,$area"
}
close $lf

puts "AREA_AUDIT_LATEST_COMPLETE"
exit
