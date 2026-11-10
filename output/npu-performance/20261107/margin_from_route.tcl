set checkpoint [file normalize [lindex $argv 0]]
set margin_out [file normalize [lindex $argv 1]]
set margin_repo [file normalize [lindex $argv 2]]
set helper [file normalize [lindex $argv 3]]
set argv [list $margin_repo $margin_out 4 50 full 8]
source $helper
file mkdir $out
cd $out
set_param general.maxThreads 4
if {[catch {
    set stage open_route
    open_checkpoint $checkpoint
    source [file join $repo hardware/fpga/scripts/constrain_npu_jtag_cdc.tcl]
    source [file join $repo hardware/fpga/scripts/audit_jtag_io.tcl]
    set clock [get_clocks -of_objects [get_pins u_clk_wiz/clk_out1]]
    if {[llength $clock]!=1 || abs([get_property PERIOD $clock]-20.0)>0.001} {error "Expected original 50 MHz clock"}
    report_clocks -file [file join $out original_clocks.rpt]
    report_timing -delay_type max -max_paths 1 -file [file join $out original_worst.rpt]
    set stage margin_optimization
    set_clock_uncertainty -setup 0.350 $clock
    report_timing -delay_type max -max_paths 1 -file [file join $out tightened_worst.rpt]
    phys_opt_design -directive AggressiveExplore
    route_design -directive Explore
    # Return user uncertainty to the original zero. Automatic jitter and MMCM
    # phase error remain active throughout, and clock period stays 20 ns.
    set_clock_uncertainty -setup 0.000 $clock
    set stage signoff
    report_clocks -file [file join $out route_clocks.rpt]
    audit_npu_jtag_cdc [file join $out route_npu_jtag_cdc.tsv]
    audit_jtag_io [file join $out io_audit]
    write_checkpoint -force [file join $out route.dcp]
    reports route
    report_timing -delay_type max -max_paths 50 -nworst 1 -input_pins -nets -file [file join $out route_setup_paths.rpt]
    report_timing -delay_type min -max_paths 20 -nworst 1 -input_pins -nets -file [file join $out route_hold_paths.rpt]
    report_cdc -details -file [file join $out route_cdc.rpt]
    reject_critical_npu_jtag_cdc [file join $out route_cdc.rpt]
    bram_guard route
    lassign $timing_values wns tns whs
    if {[llength $timing_values]!=3 || $wns<0.100 || $whs<0} {error "Margin acceptance failed WNS=$wns WHS=$whs"}
    set metrics [open [file join $out route_metrics.tsv] w]
    puts $metrics "requested_mhz\t50"
    puts $metrics "period_ns\t[get_property PERIOD $clock]"
    puts $metrics "wns_ns\t$wns"
    puts $metrics "whs_ns\t$whs"
    puts $metrics "optimization_extra_setup_uncertainty_ns\t0.350"
    puts $metrics "signoff_extra_setup_uncertainty_ns\t0.000"
    close $metrics
    set last_stage route
    set stage write_bitstream
    write_bitstream -force [file join $out fpga_top.bit]
    set last_stage write_bitstream
    set stage complete
    set result PASS
} failure options]} {
    puts stderr $failure
    puts stderr [dict get $options -errorinfo]
    summary
    exit 1
}
set failure ""
summary
puts "PASS margin implementation; original 50 MHz constraints restored"
exit 0
