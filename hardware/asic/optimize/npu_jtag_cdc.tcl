# Cadence mapped-netlist mailbox constraints. Intentionally no clock-group cut.
# Unsupported options, renamed registers or missing timing paths are fatal.
namespace eval npu_cdc {
    variable bound 20.0
    variable cells
}

proc npu_cdc::index_cells {} {
    variable cells
    set cells [dict create]
    foreach_in_collection cell [get_cells -hierarchical *] {
        set name [lindex [get_object_name $cell] 0]
        # Genus may retain hierarchy; Innovus commonly imports flattened names.
        set key [string map {/ _} $name]
        if {[dict exists $cells $key]} {error "Ambiguous CDC cell: $key"}
        dict set cells $key $name
    }
}

proc npu_cdc::pin {stem bit terminal} {
    variable cells
    set key i_host_${stem}_reg
    if {$bit >= 0} {append key \[$bit\]}
    if {![dict exists $cells $key]} {error "Missing CDC register: $key"}
    set cell [dict get $cells $key]
    # Filter by literal full name: [] must not act as collection wildcards.
    set result [get_pins -of_objects [get_cells -hierarchical -filter "full_name == \"$cell\""] \
        -filter "name == \"$terminal\" || full_name == \"$cell/$terminal\""]
    if {[sizeof_collection $result] != 1} {error "Missing/ambiguous CDC pin: $cell/$terminal"}
    return $result
}

proc npu_cdc::payloads {} {
    set result {}
    foreach {target offset width} {cmd_data_o 0 32 cmd_addr_o 32 32 cmd_op_o 64 8} {
        for {set bit 0} {$bit < $width} {incr bit} {
            lappend result [list request [pin req_payload [expr {$offset+$bit}] Q] [pin $target $bit D]]
        }
    }
    foreach {field width} {data 32 error 5} {
        for {set bit 0} {$bit < $width} {incr bit} {
            lappend result [list response [pin rsp_mailbox_$field $bit Q] [pin reply_$field $bit D]]
        }
    }
    return $result
}

proc npu_cdc::apply {} {
    variable bound
    index_cells
    foreach spec [payloads] {
        lassign $spec direction from to
        # Cadence option, not Vivado -datapath_only. Fail if unsupported.
        # Q->D bounds exclude launch tCQ; 20 ns is conservatively below the
        # earliest ideal capture (~40 ns at 50 MHz). Protocol holds the source
        # until acknowledgement. Independent clock-phase hold has no meaning.
        set_max_delay $bound -ignore_clock_latency -from $from -to $to
        set_false_path -hold -from $from -to $to
    }
    foreach {source target} {req_toggle req_sync ack_toggle ack_sync} {
        set_false_path -from [pin $source -1 Q] -to [pin $target 0 D]
    }
    # TSMC180 mapped reset pin is CDN. A different library must supply an
    # explicit reviewed adaptation rather than silently omitting reset checks.
    foreach bank {tck_reset_sync sys_reset_sync} {
        foreach bit {0 1} {set_false_path -to [pin $bank $bit CDN]}
    }
}

proc npu_cdc::timed {from to mode} {
    set paths [report_timing -collection -from $from -to $to $mode -max_paths 1]
    if {[sizeof_collection $paths] != 1} {error "CDC path missing/cut: [get_object_name $from] -> [get_object_name $to] $mode"}
    set slack [get_property $paths slack]
    if {![string is double -strict $slack] || abs($slack) > 1e6 || $slack < 0} {
        error "Invalid/negative CDC slack: $slack"
    }
    return $paths
}

proc npu_cdc::audit {filename} {
    variable bound
    index_cells
    set f [open $filename w]
    puts $f "kind\tfrom\tto\tmode\tarrival_ns\trequired_ns\tslack_ns"
    foreach spec [payloads] {
        lassign $spec direction from to
        set path [timed $from $to -late]
        set arrival [get_property $path arrival_time]
        set required [get_property $path required_time]
        set slack [get_property $path slack]
        # An effective clock-independent max budget must be visible, not just
        # present in the SDC. Unsupported property semantics fail qualification.
        if {![string is double -strict $arrival] || ![string is double -strict $required] ||
            $arrival < 0 || $required <= 0 || $required > $bound+0.001 ||
            abs($required-$arrival-$slack)>0.01} {
            error "CDC effective budget not proven: $direction arrival=$arrival required=$required slack=$slack"
        }
        puts $f "$direction\t[lindex [get_object_name $from] 0]\t[lindex [get_object_name $to] 0]\tlate\t$arrival\t$required\t$slack"
    }
    foreach bank {req_sync ack_sync} {
        set from [pin $bank 0 Q]; set to [pin $bank 1 D]
        foreach mode {-late -early} {
            set path [timed $from $to $mode]
            puts $f "$bank\t[lindex [get_object_name $from] 0]\t[lindex [get_object_name $to] 0]\t[string range $mode 1 end]\tNA\tNA\t[get_property $path slack]"
        }
    }
    close $f
}

# Genus 19 has setup-only report_timing (no -late/-early) and reports slack
# in picoseconds; arrival/required properties are not reliable there. The
# mapped netlist is therefore audited for presence and nonnegative setup slack
# of every constrained path; the routed Innovus audit above checks budgets.
proc npu_cdc::audit_synth {filename} {
    index_cells
    set f [open $filename w]
    puts $f "kind\tfrom\tto\tmode\tslack_ns"
    set specs [payloads]
    foreach bank {req_sync ack_sync} {lappend specs [list $bank [pin $bank 0 Q] [pin $bank 1 D]]}
    foreach spec $specs {
        lassign $spec kind from to
        set paths [report_timing -collection -from $from -to $to -max_paths 1]
        if {[sizeof_collection $paths] != 1} {error "CDC path missing/cut: [get_object_name $from] -> [get_object_name $to]"}
        set slack [get_property $paths slack]
        if {![string is double -strict $slack] || $slack < 0} {error "Invalid/negative CDC slack: $slack"}
        puts $f "$kind\t[lindex [get_object_name $from] 0]\t[lindex [get_object_name $to] 0]\tlate\t[expr {$slack/1000.0}]"
    }
    close $f
}

