# Reuse main's verified optimized logic; replace only the package pin constraint.
set repo [file normalize [lindex $argv 0]]
set original [file normalize [lindex $argv 1]]
set out [file normalize [lindex $argv 2]]
file mkdir $out
cd $out
set_param general.maxThreads 4
open_checkpoint [file join $original opt.dcp]
source [file join $repo hardware/fpga/ego1_pins.xdc]
if {[get_property PACKAGE_PIN [get_ports tck_i]] ne "H16"} {error "TCK must use H16"}
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets -of_objects [get_ports tck_i]]
place_design
route_design
write_checkpoint -force [file join $out route.dcp]
report_utilization -file [file join $out route_utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $out route_timing_summary.rpt]
report_drc -file [file join $out route_drc.rpt]
report_methodology -file [file join $out route_methodology.rpt]
source [file join $repo hardware/fpga/check_bram_drc.tcl]
set rams [get_cells -hierarchical -filter {NAME =~ u_soc/i_mainmem/i_mainmem/* && REF_NAME =~ RAMB36*}]
if {[llength $rams] != 2} {error "Expected 2 main SRAM RAMB36 cells"}
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
set wns [get_property SLACK $setup]
set whs [get_property SLACK $hold]
if {$wns < 0 || $whs < 0} {error "Timing failed WNS=$wns WHS=$whs"}
write_bitstream -force [file join $out fpga_top.bit]
set f [open [file join $out summary.md] w]
puts $f "# main TCK H16 reimplementation\n\nResult: PASS\n"
puts $f "CPU/NPU logic from main 9d25b09 opt.dcp. Only tck_i PACKAGE_PIN changed E15 -> H16 (J5 pin 21)."
puts $f "Clocking: 50 MHz SoC, 1 MHz TCK constraint; initial adapter speed 10 kHz."
puts $f "WNS: $wns ns; WHS: $whs ns. Main SRAM: 2 RAMB36. Configuration/BRAM DRC PASS."
puts $f "Non-dedicated TCK routing allowed on the external TCK input net."
close $f
puts "LAB6_TCK_H16_BUILD=PASS"
exit
