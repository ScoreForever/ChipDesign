# Resume a verified synthesis checkpoint; do not synthesize or program a board.
set checkpoint [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
set_param general.maxThreads 4
open_checkpoint $checkpoint
source [file normalize [lindex $argv 2]]
source [file normalize [lindex $argv 3]]
audit_npu_jtag_cdc [file join $out checkpoint_npu_jtag_cdc.tsv]
opt_design
write_checkpoint -force [file join $out opt.dcp]
if {[catch {place_design} failure options]} {
    set fh [open [file join $out vivado.log] r]
    set log [read $fh]
    close $fh
    if {![regexp {ERROR: \[Place 30-574\]} $log] || ![string match "*tck_i*" $log]} {
        return -options $options $failure
    }
    close_design
    open_checkpoint [file join $out opt.dcp]
    set net [get_nets -of_objects [get_ports tck_i]]
    if {[llength $net] != 1} {error "ambiguous JTAG clock input"}
    set_property CLOCK_DEDICATED_ROUTE FALSE $net
    set fh [open [file join $out jtag_route_override.txt] w]
    puts $fh "EGo1 E15 is not a dedicated clock pin; CLOCK_DEDICATED_ROUTE FALSE on $net after Place 30-574"
    close $fh
    place_design
}
phys_opt_design
route_design
# One measured post-route optimization pass for near-critical setup paths.
# Retain the original clock constraints; do not waive failing paths.
set initial_wns [get_property SLACK [lindex [get_timing_paths -delay_type max -max_paths 1] 0]]
if {$initial_wns<0} {
    phys_opt_design -directive AggressiveExplore
    route_design -directive Explore
}
audit_npu_jtag_cdc [file join $out route_npu_jtag_cdc.tsv]
audit_jtag_io [file join $out io_audit]
write_checkpoint -force [file join $out route.dcp]
report_utilization -file [file join $out route_utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $out route_timing_summary.rpt]
# Keep multiple complete paths: the summary's single worst path cannot show
# whether closure improved the whole gather/store cone or just moved its end.
report_timing -delay_type max -max_paths 50 -nworst 1 -input_pins -nets -file [file join $out route_setup_paths.rpt]
report_timing -delay_type min -max_paths 20 -nworst 1 -input_pins -nets -file [file join $out route_hold_paths.rpt]
report_high_fanout_nets -max_nets 50 -file [file join $out route_high_fanout.rpt]
report_utilization -hierarchical -file [file join $out route_utilization_hierarchical.rpt]
report_cdc -details -file [file join $out route_cdc.rpt]
report_clock_interaction -file [file join $out route_clock_interaction.rpt]
report_drc -file [file join $out route_drc.rpt]
set drc_errors [llength [get_drc_violations -quiet -filter {SEVERITY == "Error"}]]
set wns [get_property SLACK [lindex [get_timing_paths -delay_type max -max_paths 1] 0]]
set whs [get_property SLACK [lindex [get_timing_paths -delay_type min -max_paths 1] 0]]
set result PASS
if {$wns<0 || $whs<0} {set result TIMING_FAILED}
if {$drc_errors>0} {set result DRC_FAILED}
set fh [open [file join $out metrics.tsv] w]
puts $fh "result\t$result"
puts $fh "wns_ns\t$wns"
puts $fh "whs_ns\t$whs"
puts $fh "drc_errors\t$drc_errors"
close $fh
puts "CHECKPOINT_IMPLEMENTATION $result WNS=$wns WHS=$whs (no bitstream)"
close_design
exit 0
