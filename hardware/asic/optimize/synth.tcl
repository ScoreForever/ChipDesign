source config.tcl
set_db max_cpus_per_server $threads
set_db init_hdl_search_path [list $source_root]
set_db library [list $std_lib $macro_lib]
set fh [open $source_root/hardware/soc/sim/filelists/chipdesign_nocpu.f r]
set sources {}
foreach line [split [read $fh] \n] {
    set line [string trim $line]
    if {$line ne "" && ![string match +* $line]} {lappend sources $source_root/$line}
}
close $fh
# Both stores map to the course SRAM macro; the waveform RAM is an FPGA/simulation
# debug option and is explicitly excluded from the ASIC build, as are the
# per-operator summaries (global performance counters remain).
read_hdl -sv -define [list SYNTHESIS NPU_USE_RA1SHD_MODEL NPU_USE_RA1SHD_ACTIVATION NPU_ENABLE_WAVE_TRACE=0 NPU_ENABLE_OP_TRACE=0 NPU_ARRAY_COLS=$array_cols] $sources
elaborate my_soc_top
check_design -unresolved > check_design.rpt
set_units -time ns -capacitance pF
set ::env(EDA_CLOCK_NS) 20
set ::env(EDA_IO_DELAY_NS) 2
set ::env(EDA_CLOCK_UNCERTAINTY_NS) 0.2
source $source_root/hardware/soc/synth/scripts/soc_constraints.tcl
report_clocks > clocks.rpt
syn_generic
syn_map
syn_opt
report_area -depth 4 > area.rpt
report_timing > timing.rpt
write_hdl > mapped.v
write_sdc > mapped.sdc
set total [get_db current_design .area]
set macro_area 0
set macro_count 0
foreach inst [get_db insts] {
    if {[get_db $inst .base_cell.name] eq "RA1SHD_2048x32M8"} {
        set macro_area [expr {$macro_area+[get_db $inst .area]}]
        incr macro_count
    }
}
if {$macro_count != 2 || $total <= $macro_area} {error "Invalid mapped area or SRAM count (expected two macros)"}
set fp [open area.json w]
puts $fp [format {"total_area_um2":%.9f,"standard_cell_area_um2":%.9f,"macro_area_um2":%.9f,"macro_count":%d} $total [expr {$total-$macro_area}] $macro_area $macro_count]
close $fp
# Wrap the JSON object without relying on Tcl braces being emitted literally.
set fp [open area.json r];set data [read $fp];close $fp
set fp [open area.json w];puts $fp "\173$data\175";close $fp
puts "PNR_OPT_SYNTHESIS_COMPLETE"
exit
