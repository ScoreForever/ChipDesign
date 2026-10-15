# Analyze an existing routed checkpoint. No implementation or power optimization.
set script_dir [file dirname [info script]]
set dcp [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
set saif [lindex $argv 2]
file mkdir $out
cd $out
open_checkpoint $dcp
source [file join $script_dir export_ego1_activity_nets.tcl]
export_activity_nets [file join $out design_nets.tsv]
# Micro-watts prevent small storage blocks being suppressed/rounded away.
set_units -power uW
report_power -hier all -hierarchical_depth 0 -advisory -file vectorless_power.rpt
report_power -hier all -format xml -file vectorless_power.xml
if {$saif ne ""} {
    # The converter maps <testbench>/workload/dut to fpga_top/u_soc.
    # Vivado design nets start with u_soc (the top module is not part of NAME).
    read_saif -strip_path fpga_top -out_file saif_unmatched.rpt $saif
    report_power -hier all -hierarchical_depth 0 -advisory -file saif_power.rpt
    report_power -hier all -format xml -file saif_power.xml
    report_switching_activity -signal_rate -static_probability -hier \
        [get_nets -hierarchical -filter {NAME =~ u_soc/*}] -file saif_switching.rpt
}
close_design
exit 0
