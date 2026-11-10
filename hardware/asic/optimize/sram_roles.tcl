# Resolve placement roles without relying on the shared-store parent name.
# ABI 5: bank 0 is the lower activation bank; bank 1 contains the model image
# at its high end and also holds any activation spill. Legacy stores remain
# accepted so historical netlists can still be reproduced.
proc npu_sram_role {name} {
    set banks {}
    foreach bank {0 1} {
        if {[string first [format {g_bank[%d]} $bank] $name]>=0 ||
            [regexp [format {(^|[/._])g_bank_%d_} $bank] $name]} {
            lappend banks $bank
        }
    }
    if {[llength $banks]==1} {
        return [expr {[lindex $banks 0]==1 ? "model" : "activation"}]
    }
    if {[string first g_bank $name]>=0} {error "Unknown or ambiguous shared SRAM bank: $name"}
    if {[string match *model_store* $name]} {return model}
    if {[string match *activation* $name]} {return activation}
    error "Unknown SRAM instance role: $name"
}

# dbGet serializes a name containing bus brackets as a one-element Tcl list.
# Physical commands that expect a scalar must not receive the list's braces.
proc npu_sram_name {query_result} {
    if {[llength $query_result]!=1} {error "Expected one SRAM name: $query_result"}
    return [lindex $query_result 0]
}
