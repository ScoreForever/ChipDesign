# ABI 2 platform configuration. No fixed-model tile/accumulator switches.
set use_model_sram 1
if {[info exists ::env(EDA_NPU_MODEL_SRAM)]} {
    set use_model_sram $::env(EDA_NPU_MODEL_SRAM)
    if {$use_model_sram ni {0 1}} {error "EDA_NPU_MODEL_SRAM must be 0 or 1"}
}
set hdl_defines {SYNTHESIS}
if {[info exists ::env(EDA_NPU_COLS)]} {
    set cols $::env(EDA_NPU_COLS)
    if {$cols ni {4 8}} {error "EDA_NPU_COLS must be 4 or 8"}
    lappend hdl_defines "NPU_ARRAY_COLS=$cols"
}
