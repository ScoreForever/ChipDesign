# Run from repository root. Default: generic elaboration only.
# Mapped synthesis requires EDA_TARGET_LIBS (Tcl list of .db paths),
# EDA_CLOCK_NS, EDA_IO_DELAY_NS and EDA_CLOCK_UNCERTAINTY_NS.
set root [pwd]
set result_dir $::env(EDA_RESULT_DIR)
file mkdir $result_dir
set_app_var search_path [concat $search_path [list $root "$root/hardware/soc/rtl/include"]]
set mapped [info exists ::env(EDA_TARGET_LIBS)]
source hardware/soc/synth/scripts/nocpu_area_config.tcl
if {$mapped} {
    foreach key {EDA_CLOCK_NS EDA_IO_DELAY_NS EDA_CLOCK_UNCERTAINTY_NS} {
        if {![info exists ::env($key)]} {error "Missing $key for mapped synthesis"}
    }
    set target_libs $::env(EDA_TARGET_LIBS)
    if {$use_model_sram} {
        # DC needs a compiled .db, not the course Liberty accepted by Genus.
        if {![info exists ::env(EDA_MODEL_SRAM_DB)] || ![file isfile $::env(EDA_MODEL_SRAM_DB)]} {
            error "Set EDA_MODEL_SRAM_DB to the matching SRAM .db, or EDA_NPU_MODEL_SRAM=0 for the FF comparison"
        }
        if {[lsearch -exact $target_libs $::env(EDA_MODEL_SRAM_DB)]<0} {
            lappend target_libs $::env(EDA_MODEL_SRAM_DB)
        }
        lappend hdl_defines NPU_USE_RA1SHD_MODEL
    }
    foreach lib $target_libs {
        if {![file isfile $lib]} {error "Target library does not exist: $lib"}
    }
    set_app_var target_library $target_libs
    set_app_var link_library [concat [list *] $target_library]
}
define_design_lib WORK -path "$result_dir/work"
set fh [open hardware/soc/sim/filelists/chipdesign_nocpu.f r]
set sources {}
foreach line [split [read $fh] \n] {
    set line [string trim $line]
    if {$line eq "" || [string match +* $line] || [string match */sim/tb/* $line]} {continue}
    lappend sources $line
}
close $fh
if {![analyze -format sverilog -define $hdl_defines $sources]} {error "RTL analyze failed"}
if {![elaborate my_soc_top]} {error "RTL elaborate failed"}
current_design my_soc_top
if {![link]} {error "RTL link failed"}
redirect "$result_dir/check_design.rpt" {check_design}
write -format ddc -hierarchy -output "$result_dir/elaborated.ddc"
if {$mapped} {
    source hardware/soc/synth/scripts/soc_constraints.tcl
    compile_ultra
    redirect "$result_dir/area.rpt" {report_area -hierarchy}
    redirect "$result_dir/timing.rpt" {report_timing -max_paths 20}
    redirect "$result_dir/constraints.rpt" {report_constraint -all_violators}
    redirect "$result_dir/check_timing.rpt" {check_timing}
    write -format verilog -hierarchy -output "$result_dir/my_soc_top.v"
    write_sdc "$result_dir/my_soc_top.sdc"
    write -format ddc -hierarchy -output "$result_dir/mapped.ddc"
    puts "EDA_MAPPED_SYNTHESIS_COMPLETE"
} else {
    puts "EDA_GENERIC_ELABORATION_COMPLETE"
}
exit
