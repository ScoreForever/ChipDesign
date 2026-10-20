set input [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
open_checkpoint $input
file mkdir $out
source [file join [file dirname [info script]] ../../hardware/fpga/scripts/constrain_dmi_cdc.tcl]
audit_dmi_cdc [file join $out dmi_constraints.tsv]
report_methodology -file [file join $out route_methodology.rpt]
report_cdc -details -file [file join $out cdc_details.rpt]
report_clock_interaction -file [file join $out clock_interaction.rpt]
report_exceptions -coverage -file [file join $out exceptions_coverage.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $out route_timing_summary.rpt]
report_drc -file [file join $out route_drc.rpt]
set f [open [file join $out timing.json] w]
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
puts $f "{\"wns_ns\":$wns,\"whs_ns\":$whs}"
close $f
puts "FPGA_AUDIT_COLLECTED=PASS"
exit
