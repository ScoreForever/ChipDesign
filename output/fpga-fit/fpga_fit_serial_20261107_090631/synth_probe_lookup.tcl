set root [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
file mkdir $out
set_param general.maxThreads 4
create_project -in_memory -part xc7a35tcsg324-1
set f [open [file join $root hardware/soc/sim/filelists/chipdesign_nocpu.f] r]
set entries [split [read $f] \n]
close $f
set sources {}
foreach entry $entries {
    set entry [string trim $entry]
    if {$entry eq "hardware/npu/rtl/npu_program_core.sv"} {
        lappend sources [file normalize [lindex $argv 2]]
    } elseif {$entry ne ""} {
        lappend sources [file join $root $entry]
    }
}
set_property verilog_define {SYNTHESIS CHIPDESIGN_FPGA_MEM NPU_ENABLE_OP_TRACE=0 NPU_ENABLE_WAVE_TRACE=0} [current_fileset]
read_verilog -sv $sources
synth_design -top my_soc_top -part xc7a35tcsg324-1 -flatten_hierarchy none
report_utilization -file [file join $out synth_utilization.rpt]
report_utilization -hierarchical -file [file join $out synth_utilization_hierarchical.rpt]
puts "SOC_SYNTH_PROBE_PASS"
