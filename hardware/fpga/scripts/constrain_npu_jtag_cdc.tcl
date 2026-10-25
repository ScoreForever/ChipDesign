# Dedicated CPU-less NPU TAP CDC constraints. No CPU DMI hierarchy is used.
# req_payload is held until its request completes; rsp_mailbox is held until
# the next response. Two-stage toggle synchronizers delay the receiving capture.
# Bound bundled-data flight time by min(core period,TCK period), without cutting
# the payload paths. Only asynchronous control entry pins and reset assertion
# pins of synchronizer banks are excluded from synchronous setup/hold analysis.

proc npu_jtag_cdc_cells {stems minimum label} {
    set selectors {}
    foreach stem $stems {
        lappend selectors [format {NAME =~ "u_soc/i_host/%s_reg*"} $stem]
    }
    set cells [get_cells -hier -filter [join $selectors { || }]]
    if {[llength $cells] < $minimum} {
        error "NPU TAP CDC endpoints missing: $label ([llength $cells] < $minimum)"
    }
    return $cells
}

proc npu_jtag_cdc_pin {stem bit pin} {
    if {$bit < 0} {
        set name [format {u_soc/i_host/%s_reg} $stem]
    } else {
        set name [format {u_soc/i_host/%s_reg[%d]} $stem $bit]
    }
    set cells [get_cells -hier -filter [format {NAME == "%s"} $name]]
    if {[llength $cells] != 1} {error "NPU TAP CDC register missing/ambiguous: $name"}
    set pins [get_pins -of_objects $cells -filter [format {REF_PIN_NAME == "%s"} $pin]]
    if {[llength $pins] != 1} {error "NPU TAP CDC pin missing/ambiguous: $name/$pin"}
    return $pins
}

proc npu_jtag_cdc_bound {} {
    set core [get_clocks -of_objects [get_pins u_clk_wiz/clk_out1]]
    set host [get_clocks tck_i]
    if {[llength $core] != 1 || [llength $host] != 1} {error "Missing NPU core/TCK clock"}
    return [expr {min([get_property PERIOD $core],[get_property PERIOD $host])}]
}

proc npu_jtag_cdc_payloads {} {
    set groups {}
    foreach {label source_stems target_stems minimum} {
        request_payload {req_payload} {cmd_op_o cmd_addr_o cmd_data_o} 72
        response_payload {rsp_mailbox_data rsp_mailbox_error} {reply_data reply_error} 37
    } {
        set source [npu_jtag_cdc_cells $source_stems $minimum $label/source]
        set target [npu_jtag_cdc_cells $target_stems $minimum $label/target]
        # Whole source registers are legal timing startpoints. Using Q pins
        # here would segment the path and discard the launching clock arc.
        set from $source
        set to [get_pins -of_objects $target -filter {REF_PIN_NAME == D}]
        if {[llength $from] < $minimum || [llength $to] < $minimum} {
            error "NPU TAP CDC payload pins missing: $label"
        }
        lappend groups [list $label $minimum $from $to]
    }
    return $groups
}

proc npu_jtag_cdc_controls {} {
    return [list \
        [list request_toggle req_toggle req_sync] \
        [list response_toggle ack_toggle ack_sync]]
}

proc npu_jtag_cdc_resets {} {
    set groups {}
    foreach {label pattern} {
        fpga_core_reset rst_sync_q_reg*
        fpga_tck_reset tck_reset_q_reg*
        tap_core_reset u_soc/i_host/sys_reset_sync_reg*
        tap_tck_reset u_soc/i_host/tck_reset_sync_reg*
    } {
        set cells [get_cells -hier -filter [format {NAME =~ "%s"} $pattern]]
        if {[llength $cells] != 2} {error "Expected two reset synchronizer stages: $label"}
        set pins [get_pins -of_objects $cells -filter {REF_PIN_NAME == CLR}]
        if {[llength $pins] != 2} {error "Expected two asynchronous CLR pins: $label"}
        lappend groups [list $label $cells $pins]
    }
    return $groups
}

proc constrain_npu_jtag_cdc {} {
    set bound [npu_jtag_cdc_bound]
    foreach group [npu_jtag_cdc_payloads] {
        lassign $group label minimum from to
        set_max_delay -datapath_only $bound -from $from -to $to
    }
    foreach group [npu_jtag_cdc_controls] {
        lassign $group label source target
        # Metastability is handled by ASYNC_REG stages, not by a synchronous
        # phase relationship between independent TCK and core clock edges.
        set_false_path -from [get_cells -of_objects [npu_jtag_cdc_pin $source -1 Q]] \
            -to [npu_jtag_cdc_pin $target 0 D]
    }
    foreach group [npu_jtag_cdc_resets] {
        lassign $group label cells pins
        # Exclude asynchronous assertion only at the synchronizer bank.
        # Downstream reset release/recovery/removal paths remain timed.
        set_false_path -to $pins
    }
}

proc npu_jtag_cdc_finite_paths {args} {
    set result {}
    foreach path [get_timing_paths -quiet {*}$args] {
        set slack [get_property SLACK $path]
        if {$slack ne "" && [string is double -strict $slack] && abs($slack)<1e9} {
            lappend result $path
        }
    }
    return $result
}

proc audit_npu_jtag_cdc {path} {
    set f [open $path w]
    puts $f "group\tkind\tsource_registers\ttarget_pins\tpaths\tbound_ns\tmin_slack_ns\tlaunch_clock\tcapture_clock"
    set bound [npu_jtag_cdc_bound]
    foreach group [npu_jtag_cdc_payloads] {
        lassign $group label minimum from to
        set paths [npu_jtag_cdc_finite_paths -from $from -to $to -delay_type max -max_paths 10000 -nworst 1]
        if {[llength $paths] != [llength $to]} {
            error "NPU bundled payload paths cut/missing: $label ([llength $paths]/[llength $to])"
        }
        set slack 1e9
        foreach timing $paths {
            if {abs([get_property REQUIREMENT $timing]-$bound)>0.001 ||
                [get_property EXCEPTION $timing] eq ""} {
                error "NPU bundled payload lacks the effective max-delay budget: $label"
            }
            set slack [expr {min($slack,[get_property SLACK $timing])}]
        }
        set first [lindex $paths 0]
        puts $f "$label\tbundled_max_delay\t[llength $from]\t[llength $to]\t[llength $paths]\t$bound\t$slack\t[get_property STARTPOINT_CLOCK $first]\t[get_property ENDPOINT_CLOCK $first]"
        if {$slack < 0} {error "NPU bundled payload max delay failed: $label"}
    }
    foreach group [npu_jtag_cdc_controls] {
        lassign $group label source target
        set entry [npu_jtag_cdc_pin $target 0 D]
        set cut [npu_jtag_cdc_finite_paths -from [get_cells -of_objects [npu_jtag_cdc_pin $source -1 Q]] -to $entry -max_paths 1]
        if {[llength $cut]} {error "Asynchronous toggle entry still synchronously timed: $label"}
        set first_q [npu_jtag_cdc_pin $target 0 Q]
        set second_d [npu_jtag_cdc_pin $target 1 D]
        set stage_cells [get_cells -of_objects [list $entry $second_d]]
        foreach cell $stage_cells {
            if {[string tolower [get_property ASYNC_REG $cell]] ni {true 1 yes}} {
                error "NPU toggle stage lacks ASYNC_REG: $cell"
            }
        }
        if {[get_nets -of_objects $first_q] ne [get_nets -of_objects $second_d]} {
            error "NPU toggle synchronizer has logic between stages: $label"
        }
        foreach type {max min} {
            set timed [npu_jtag_cdc_finite_paths -from [get_cells -of_objects $first_q] -to $second_d -delay_type $type -max_paths 1]
            if {[llength $timed] != 1 || [get_property EXCEPTION [lindex $timed 0]] ne ""} {
                error "NPU synchronizer second stage missing/cut: $label/$type"
            }
            set timing [lindex $timed 0]
            puts $f "$label\tsecond_stage_$type\t1\t1\t1\t[get_property REQUIREMENT $timing]\t[get_property SLACK $timing]\t[get_property STARTPOINT_CLOCK $timing]\t[get_property ENDPOINT_CLOCK $timing]"
        }
        puts $f "$label\tasync_entry_D_only\t1\t1\t0\tNA\tNA\tNA\tNA"
    }
    foreach group [npu_jtag_cdc_resets] {
        lassign $group label cells pins
        foreach cell $cells {
            if {[string tolower [get_property ASYNC_REG $cell]] ni {true 1 yes}} {
                error "NPU reset stage lacks ASYNC_REG: $cell"
            }
        }
        set timed [npu_jtag_cdc_finite_paths -to $pins -max_paths 10000]
        if {[llength $timed]} {error "Reset synchronizer asynchronous assertion still timed: $label"}
        puts $f "$label\tasync_CLR_only\tNA\t[llength $pins]\t0\tNA\tNA\tNA\tNA"
    }
    close $f
}
