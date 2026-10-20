# Pure Tcl profile selection shared by the Genus/DC entry points.
set use_weight_sram 1
if {[info exists ::env(EDA_NPU_WEIGHT_SRAM)]} {
    set use_weight_sram $::env(EDA_NPU_WEIGHT_SRAM)
    if {$use_weight_sram ni {0 1}} {error "EDA_NPU_WEIGHT_SRAM must be 0 or 1"}
}
set hdl_defines {SYNTHESIS}
if {!$use_weight_sram} {lappend hdl_defines NPU_WEIGHT_FF}
foreach {env_name define_name allowed} {
    EDA_NPU_TILE NPU_SPATIAL_TILE {4 8 16}
    EDA_NPU_ARRAY_ACC NPU_ARRAY_ACC_WIDTH {18 32}
    EDA_NPU_PSUM NPU_PSUM_WIDTH {23 32}
} {
    if {[info exists ::env($env_name)]} {
        set value $::env($env_name)
        if {$value ni $allowed} {error "invalid $env_name: $value"}
        lappend hdl_defines "$define_name=$value"
    }
}
