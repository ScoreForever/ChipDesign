# Inspect effective paths; merely finding set_input/output_delay text is not evidence.
proc audit_jtag_io {out} {
    file mkdir $out
    set f [open [file join $out jtag_io.tsv] w]
    puts $f "port\ttype\tpaths\tio_delay_ns\tlaunch_edge_ns\tcapture_edge_ns\tmin_slack_ns\tdirect_paths\tequivalent_paths"
    set period [get_property PERIOD [get_clocks tck_i]]
    if {abs($period-1000.0)>0.001} {error "JTAG profile requires 1000ns clock constraint"}
    foreach port {td_i tms_i td_o} {
        foreach type {max min} {
            if {$port eq "td_o"} {
                set paths [get_timing_paths -to [get_ports $port] -delay_type $type -max_paths 10000 -nworst 1]
                set property OUTPUT_DELAY
                set expected [expr {$type eq "max" ? 41.0 : -20.0}]
            } else {
                set paths [get_timing_paths -from [get_ports $port] -delay_type $type -max_paths 10000 -nworst 1]
                set property INPUT_DELAY
                set expected [expr {$type eq "max" ? 32.5 : -25.0}]
            }
            if {![llength $paths]} {error "JTAG data path missing/cut: $port/$type"}
            set capture [expr {$type eq "max" ? $period : 0.0}]
            set slack 1e9
            set direct 0
            set equivalent 0
            foreach p $paths {
                set d [get_property $property $p]
                set l [get_property STARTPOINT_CLOCK_EDGE $p]
                set c [get_property ENDPOINT_CLOCK_EDGE $p]
                if {abs($d-$expected)<0.001 && abs($l-$period/2)<0.001 && abs($c-$capture)<0.001} {
                    incr direct
                } elseif {$port ne "td_o" && abs($d-($expected+$period/2))<0.001 && abs($l)<0.001 && abs($c-$capture)<0.001} {
                    incr equivalent
                } elseif {$port eq "td_o" && abs($d-($expected+$period/2))<0.001 && abs($l-$period/2)<0.001 && abs($c-($capture+$period/2))<0.001} {
                    incr equivalent
                } else {error "JTAG $port/$type incorrect delay/edge window"}
                if {[get_property STARTPOINT_CLOCK $p] ne "tck_i" || [get_property ENDPOINT_CLOCK $p] ne "tck_i"} {error "Unexpected JTAG path clock"}
                if {[get_property EXCEPTION $p] ne ""} {error "JTAG data path has a timing exception"}
                set slack [expr {min($slack,[get_property SLACK $p])}]
            }
            if {$slack<0} {error "JTAG IO timing failed: $port/$type $slack"}
            set first [lindex $paths 0]
            puts $f "$port\t$type\t[llength $paths]\t[get_property $property $first]\t[get_property STARTPOINT_CLOCK_EDGE $first]\t[get_property ENDPOINT_CLOCK_EDGE $first]\t$slack\t$direct\t$equivalent"
            report_timing -of_objects $paths -file [file join $out ${port}_${type}.rpt]
        }
    }
    close $f
    # Exclusions must actually leave reset/LED outside synchronous data timing.
    foreach port {led_locked_o led_heartbeat_o led_tck_o} {
        foreach type {max min} {
            foreach p [get_timing_paths -quiet -to [get_ports $port] -delay_type $type -max_paths 1] {
                # Explicit queries can return an unconstrained object (SLACK=inf)
                # even for excluded async outputs; reject any finite timed path.
                set slack [get_property SLACK $p]
                if {$slack ne "" && abs($slack)<1e9} {error "LED output has an unintended synchronous requirement: $port"}
            }
        }
    }
    foreach type {max min} {
        foreach p [get_timing_paths -quiet -from [get_ports rst_ni] -delay_type $type -max_paths 1] {
            set slack [get_property SLACK $p]
            if {$slack ne "" && abs($slack)<1e9} {error "Physical reset assertion has a synchronous requirement"}
        }
    }
}
