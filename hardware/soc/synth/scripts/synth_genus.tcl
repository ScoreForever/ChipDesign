# Run from repository root; RTL read check unless EDA_TARGET_LIBS is set.
set result_dir $::env(EDA_RESULT_DIR)
file mkdir $result_dir
set_db init_hdl_search_path [list [pwd] "[pwd]/hardware/soc/rtl/include"]
set mapped [info exists ::env(EDA_TARGET_LIBS)]
source hardware/soc/synth/scripts/nocpu_area_config.tcl
if {$mapped} {
    foreach key {EDA_CLOCK_NS EDA_IO_DELAY_NS EDA_CLOCK_UNCERTAINTY_NS} {
        if {![info exists ::env($key)]} {error "Missing $key for mapped synthesis"}
    }
    set target_libs $::env(EDA_TARGET_LIBS)
    if {$use_model_sram} {
        set model_lib /lec/lab4/soc_cv32e40p_template_pnr-stu/src/macro/RA1SHD_2048x32M8/RA1SHD_2048x32M8_tt_1p8_25C_syn.lib
        if {[info exists ::env(EDA_MODEL_SRAM_LIB)]} {set model_lib $::env(EDA_MODEL_SRAM_LIB)}
        if {![file isfile $model_lib]} {
            error "Missing model SRAM Liberty; set EDA_MODEL_SRAM_LIB, or EDA_NPU_MODEL_SRAM=0 for the FF comparison"
        }
        if {[lsearch -exact $target_libs $model_lib]<0} {lappend target_libs $model_lib}
        lappend hdl_defines NPU_USE_RA1SHD_MODEL
        puts "NPU_MODEL_SRAM_LIB=$model_lib"
    }
    foreach lib $target_libs {
        if {![file isfile $lib]} {error "Target library does not exist: $lib"}
    }
    set_db library $target_libs
}
set fh [open hardware/soc/sim/filelists/chipdesign_nocpu.f r]
set sources {}
foreach line [split [read $fh] \n] {
    set line [string trim $line]
    if {$line eq "" || [string match +* $line] || [string match */sim/tb/* $line]} {continue}
    lappend sources $line
}
close $fh
read_hdl -sv -define $hdl_defines $sources
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
