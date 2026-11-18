# External Week 3 STA. config_week3.tcl refers only to a private checkpoint copy.
source config_week3.tcl
source $opt_dir/npu_jtag_cdc.tcl
source $opt_dir/sram_roles.tcl
restoreDesign work/final.enc.dat my_soc_top
create_constraint_mode -name cm_func -sdc_files [list $sdc]
foreach {corner suffix macro temp} {ss125 wc ss_1p62_125C 125 tt25 tc tt_1p8_25C 25 ff0 bc ff_1p98_0C 0 ffm40 lt ff_1p98_-40C -40} {
 create_library_set -name libs_$corner -timing [list $std/tcb018gbwp7t${suffix}.lib $io/tpd018nv${suffix}.lib $sram/RA1SHD_2048x32M8_${macro}_syn.lib]
 # Only a typical captable is available: these are temperature-scaled typical RC.
 create_rc_corner -name rc_$corner -temperature $temp -cap_table $captable
 create_delay_corner -name dc_$corner -library_set libs_$corner -rc_corner rc_$corner
 if {[file exists $latency_sdc]} {
  create_analysis_view -name view_$corner -constraint_mode cm_func -delay_corner dc_$corner -latency_file $latency_sdc
 } else {
  create_analysis_view -name view_$corner -constraint_mode cm_func -delay_corner dc_$corner
 }
}
set views {view_ss125 view_tt25 view_ff0 view_ffm40}
set_analysis_view -setup $views -hold $views
set_interactive_constraint_modes {cm_func}
npu_cdc::apply
set_interactive_constraint_modes {}
setAnalysisMode -analysisType onChipVariation -cppr both
extractRC
proc worst {mode args} {
 set col [report_timing -collection $mode -max_paths 1 {*}$args]
 if {[sizeof_collection $col]==0} {return {NA NA NA NA NA}}
 if {[sizeof_collection $col]!=1} {error "Missing timing path"}
 # Consume every property before another report_timing invalidates the handle.
 set row {}
 foreach prop {slack launching_point capturing_point arrival required} {
  set v [get_property $col $prop]
  if {$prop in {launching_point capturing_point}} {
   if {[catch {set n [get_object_name $v]}] == 0 && $n ne ""} {set v [lindex $n 0]}
  }
  lappend row $v
 }
 return $row
}
set negative 0
foreach v $views {
 set_analysis_view -setup [list $v] -hold [list $v]
 if {[lindex [worst -early] 0]<0} {set negative 1}
}
if {$RUN_HOLD_ECO && $negative} {
 set_analysis_view -setup $views -hold $views
 setOptMode -fixHoldAllowSetupTnsDegrade false
 optDesign -postRoute -hold
 extractRC
}
set f [open timing.tsv w]
puts $f "corner\tmode\twns\tstart\tend\tarrival\trequired\ttns_2000\tviolating_endpoints_2000"
set sf [open sram_interface_timing.csv w]
puts $sf "corner,instance,role,setup_to,hold_to,setup_from,hold_from"
set cf [open cdc_4corners.tsv w]
puts $cf "corner\tkind\tfrom\tto\tmode\tarrival_ns\trequired_ns\tslack_ns"
foreach corner {ss125 tt25 ff0 ffm40} {
 set_analysis_view -setup [list view_$corner] -hold [list view_$corner]
 foreach {mode label} {-late setup -early hold} {
  set row [worst $mode]
  if {$corner eq "ss125" && $mode eq "-late"} {set sswns [lindex $row 0]}
  set col [report_timing -collection $mode -nworst 1 -max_paths 2000 -max_slack 0]
  set tns 0; set n 0
  foreach_in_collection path $col {set s [get_property $path slack]; if {$s<0} {set tns [expr {$tns+$s}]; incr n}}
  puts $f "$corner\t$label\t[join $row \t]\t$tns\t$n"
  report_timing $mode -max_paths 20 > ${label}_${corner}.rpt
 }
 foreach inst [dbGet -p2 top.insts.cell.name RA1SHD_2048x32M8] {
  set name [npu_sram_name [dbGet $inst.name]]
  set cell_col [get_cells -hierarchical -filter "full_name == \"$name\""]
  set row [list $corner $name [npu_sram_role $name]]
  foreach direction {-to -from} {
   if {$direction eq "-to"} {
    set pins [get_pins -of_objects $cell_col -filter "direction == in && name != CLK"]
   } else {
    set pins [get_pins -of_objects $cell_col -filter "name == CLK"]
   }
   foreach mode {-late -early} {if {[sizeof_collection $pins]==0 || [catch {worst $mode $direction $pins} result]} {lappend row NA} else {lappend row [lindex $result 0]}}
  }
  puts $sf [join $row ,]
 }
 npu_cdc::index_cells
 set specs [npu_cdc::payloads]
 foreach bank {req_sync ack_sync} {
  foreach mode {-late -early} {lappend specs [list $bank [npu_cdc::pin $bank 0 Q] [npu_cdc::pin $bank 1 D] $mode]}
 }
 foreach spec $specs {
  lassign $spec kind from to mode
  if {$mode eq ""} {set mode -late}
  lassign [worst $mode -from $from -to $to] slack start end arrival required
  if {![string is double -strict $slack] || abs($slack) > 1e6 || $slack < 0} {error "CDC failed: $corner $kind $mode slack=$slack"}
  if {$kind in {request response}} {
   if {![string is double -strict $arrival] || ![string is double -strict $required] || $arrival < 0 || $required <= 0 || $required > 20.001 || abs($required-$arrival-$slack)>0.01} {
    error "CDC budget mismatch: $corner $kind arrival=$arrival required=$required slack=$slack"
   }
  }
  puts $cf "$corner\t$kind\t[lindex [get_object_name $from] 0]\t[lindex [get_object_name $to] 0]\t[string range $mode 1 end]\t$arrival\t$required\t$slack"
 }
}
close $cf; close $sf
set period_ss_ns [expr {ceil(20.0-$sswns+1.0)}]
set half_ss_ns [expr {$period_ss_ns / 2.0}]
set fd [open $sdc]; set text [read $fd]; close $fd
if {![regsub -- {-name\s+core_clk\s+-period\s+[0-9.eE+\-]+(?:\s+-waveform\s+\x7b[^\x7d]+\x7d)?} $text "-name core_clk -period $period_ss_ns -waveform {0 $half_ss_ns}" text]} {error "core_clk definition missing"}
set fd [open ss_derated.sdc w]; puts $fd $text; close $fd
create_constraint_mode -name cm_ss_derated -sdc_files {ss_derated.sdc}
if {[file exists $latency_sdc]} {
 create_analysis_view -name view_ss_derated -constraint_mode cm_ss_derated -delay_corner dc_ss125 -latency_file $latency_sdc
} else {
 create_analysis_view -name view_ss_derated -constraint_mode cm_ss_derated -delay_corner dc_ss125
}
set_analysis_view -setup {view_ss_derated} -hold {view_ss_derated}
set_interactive_constraint_modes {cm_ss_derated}
set npu_cdc::bound $period_ss_ns
npu_cdc::apply
set_interactive_constraint_modes {}
extractRC
foreach {mode label} {-late setup -early hold} {
 set row [worst $mode]
 puts $f "SS125_DERATED\t$label\t[join $row \t]\tNA\tNA"
 if {[lindex $row 0]<0} {error "Derated SS125 failed"}
 report_timing $mode -max_paths 20 > ${label}_SS125_DERATED.rpt
}
close $f
puts "WEEK3_STA_DONE period_ss_ns=$period_ss_ns"
set_analysis_view -setup {view_tt25} -hold {view_tt25}
exit
