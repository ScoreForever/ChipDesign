# Endpoint-specific constraints for the two-phase DMI mailbox. Data is held
# until the synchronized acknowledgement returns. Limit crossing flight time
# to min(TCK, system period), as required by cdc_2phase.sv.
proc dmi_cdc_groups {} {
    set groups {}
    foreach channel {req resp} {
        set base u_soc/i_dmi_jtag/i_dmi_cdc/i_cdc_$channel
        foreach {kind source sink} {
            data i_src/data_src_q_reg* i_dst/data_dst_q_reg*
            req i_src/req_src_q_reg i_dst/req_dst_q_reg
            ack i_dst/ack_dst_q_reg i_src/ack_src_q_reg
        } {
            set from [get_cells -hier -filter "NAME =~ $base/$source"]
            set to [get_cells -hier -filter "NAME =~ $base/$sink"]
            if {![llength $from] || [llength $from] != [llength $to]} {
                error "DMI endpoints missing or unequal: $channel/$kind"
            }
            lappend groups [list $channel/$kind $from $to]
        }
    }
    return $groups
}
proc dmi_cdc_bound {} {
    set sys [get_clocks -of_objects [get_pins u_clk_wiz/clk_out1]]
    if {[llength $sys] != 1} {error "Missing system clock"}
    return [expr {min([get_property PERIOD $sys], [get_property PERIOD [get_clocks tck_i]])}]
}
proc constrain_dmi_cdc {} {
    set bound [dmi_cdc_bound]
    foreach group [dmi_cdc_groups] {
        lassign $group name from to
        set_max_delay -datapath_only $bound -from $from -to $to
    }
    # Only asynchronous assertion pins of the TCK reset synchronizer are cut.
    # Recovery/removal on its downstream TCK-domain registers remains timed.
    set reset_cells [get_cells -hier -filter {NAME =~ u_soc/i_rstgen_tck/*synch_regs_q_reg*}]
    set reset_pins [get_pins -of_objects $reset_cells -filter {REF_PIN_NAME == CLR}]
    if {[llength $reset_pins] != 4} {error "Expected four TCK reset synchronizer CLR pins"}
    set_false_path -to $reset_pins
    # Physical pushbutton is asynchronous. Exclude only assertion into the
    # two top-level reset synchronizer banks (system and LED TCK domains).
    set button_regs [get_cells -hier -filter {NAME =~ rst_sync_q_reg* || NAME =~ tck_reset_q_reg*}]
    set button_clr [get_pins -of_objects $button_regs -filter {REF_PIN_NAME == CLR}]
    if {[llength $button_clr] != 4} {error "Expected four top-level reset synchronizer CLR pins"}
    set_false_path -from [get_ports rst_ni] -to $button_clr
}
proc audit_dmi_cdc {path} {
    set f [open $path w]
    puts $f "group\tendpoints\tpaths\tbound_ns\tmin_slack_ns"
    set bound [dmi_cdc_bound]
    foreach group [dmi_cdc_groups] {
        lassign $group name from to
        set paths [get_timing_paths -from $from -to $to -delay_type max -max_paths 10000 -nworst 1]
        if {[llength $paths] != [llength $to]} {error "DMI paths cut or missing: $name"}
        set slack 1e9
        foreach p $paths {
            if {abs([get_property REQUIREMENT $p] - $bound) > 0.001} {
                error "DMI effective requirement differs from max_delay: $name"
            }
            set slack [expr {min($slack, [get_property SLACK $p])}]
        }
        puts $f "$name\t[llength $to]\t[llength $paths]\t$bound\t$slack"
        if {$slack < 0} {error "DMI crossing max_delay failed: $name"}
    }
    close $f
}
