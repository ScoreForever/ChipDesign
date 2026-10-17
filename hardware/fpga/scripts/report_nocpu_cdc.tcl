# Inspect CDC in an existing CPU-less synthesis checkpoint; no netlist changes.
open_checkpoint [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
report_cdc -details -file [file join $out cdc.rpt]
report_clock_interaction -file [file join $out clock_interaction.rpt]
close_design
exit 0
