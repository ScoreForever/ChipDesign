# Run from repository root; RTL read check unless EDA_TARGET_LIBS is set.
set result_dir $::env(EDA_RESULT_DIR)
file mkdir $result_dir
set_db init_hdl_search_path [list [pwd] "[pwd]/hardware/soc/rtl/include"]
set mapped [info exists ::env(EDA_TARGET_LIBS)]
if {$mapped} {
    foreach key {EDA_CLOCK_NS EDA_IO_DELAY_NS EDA_CLOCK_UNCERTAINTY_NS} {
        if {![info exists ::env($key)]} {error "Missing $key for mapped synthesis"}
    }
    foreach lib $::env(EDA_TARGET_LIBS) {
        if {![file isfile $lib]} {error "Target library does not exist: $lib"}
    }
    set_db library $::env(EDA_TARGET_LIBS)
}
set fh [open hardware/soc/sim/filelists/chipdesign_soc.f r]
set sources {}
foreach line [split [read $fh] \n] {
    set line [string trim $line]
    if {$line eq "" || [string match +* $line] || [string match */sim/tb/* $line]} {continue}
    lappend sources $line
}
close $fh
read_hdl -sv -define SYNTHESIS $sources
if {!$mapped} {
    puts "EDA_RTL_READ_COMPLETE"
    exit
}
elaborate my_soc_top
check_design -unresolved > "$result_dir/check_design.rpt"
write_hdl > "$result_dir/elaborated.v"
if {$mapped} {
    source hardware/soc/synth/scripts/soc_constraints.tcl
    syn_generic
    syn_map
    syn_opt
    report_area > "$result_dir/area.rpt"
    report_timing > "$result_dir/timing.rpt"
    write_hdl > "$result_dir/my_soc_top.v"
    write_sdc > "$result_dir/my_soc_top.sdc"
    puts "EDA_MAPPED_SYNTHESIS_COMPLETE"
} else {
    puts "EDA_GENERIC_ELABORATION_COMPLETE"
}
exit
