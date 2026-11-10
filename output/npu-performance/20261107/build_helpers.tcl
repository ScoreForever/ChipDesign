# Non-project EGo1 build. Generated IP and all reports stay in the run directory.
set repo [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
set jobs [lindex $argv 2]
set frequency_mhz 50
set ppa_mode [expr {[llength $argv] > 3}]
set synth_only [expr {[llength $argv] > 4 && [lindex $argv 4] eq "synth_only"}]
set array_cols 8
if {[llength $argv] > 5} {set array_cols [lindex $argv 5]}
if {$array_cols ni {4 8}} {error "Array columns must be 4 or 8"}
if {[llength $argv] > 3} { set frequency_mhz [lindex $argv 3] }
if {![string is double -strict $frequency_mhz] || $frequency_mhz <= 0} {
    error "Output frequency must be positive MHz"
}
set stage setup
set last_stage none
set bram_status "Not run"
set mainmem_cells {}
set mainmem_status "Not measured"
set timing_values {}
set utilization_file ""
set route_override "Not applied"
set result FAILED
set failure ""

proc read_text {path} {
    set f [open $path r]
    set value [read $f]
    close $f
    return $value
}

proc reports {label} {
    global out utilization_file timing_values mainmem_cells mainmem_status
    set utilization_file [file join $out ${label}_utilization.rpt]
    report_utilization -file $utilization_file
    report_utilization -hierarchical -file [file join $out ${label}_utilization_hierarchical.rpt]
    set timing_file [file join $out ${label}_timing_summary.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained -file $timing_file
    report_drc -file [file join $out ${label}_drc.rpt]
    report_methodology -file [file join $out ${label}_methodology.rpt]
    set timing_values {}
    # The design timing table has WNS/TNS/failing/total/WHS/THS columns.
    if {[regexp {WNS\(ns\)[^\n]*\n[^\n]*\n\s*([-+0-9.]+)\s+([-+0-9.]+)\s+\d+\s+\d+\s+([-+0-9.]+)} [read_text $timing_file] -> wns tns whs]} {
        set timing_values [list $wns $tns $whs]
    }
    set mainmem_cells {}
    set mainmem_status "N/A: CPU-less system uses NPU-local storage; no main SRAM"
}

proc bram_guard {label} {
    global repo out bram_status
    set bram_status "FAILED at $label"
    set guard_failed [catch {source [file join $repo hardware/fpga/check_bram_drc.tcl]} guard_error guard_options]
    if {[info exists critical_bram_checks]} {
        report_drc -name chipdesign_bram_guard -checks $critical_bram_checks -file [file join $out ${label}_bram_guard.rpt]
    }
    if {$guard_failed} { return -options $guard_options $guard_error }
    set bram_status "PASS at $label: CFGBVS-1, REQP-1839, RBOR-1"
}

proc summary {} {
    global out stage last_stage result failure utilization_file timing_values mainmem_status mainmem_cells bram_status route_override frequency_mhz
    set f [open [file join $out summary.md] w]
    puts $f "# EGo1 build summary\n"
    puts $f "Result: $result"
    puts $f "Stage: $stage; last completed stage: $last_stage"
    puts $f "Part: xc7a35tcsg324-1; top: fpga_top; CPU-less JTAG controller and programmable five-operator NPU"
    puts $f "Clocking Wizard: 100 MHz input, [format %g $frequency_mhz] MHz output, locked enabled, reset disabled"
    puts $f "Log: vivado.log"
    if {$failure ne ""} { puts $f "\nFailure: $failure" }
    puts $f "\n## Resources (latest available stage)\n"
    puts $f "| Resource | Used | Available | Utilization (%) |"
    puts $f "|---|---:|---:|---:|"
    set resource_count 0
    set seen_resources {}
    if {$utilization_file ne "" && [file exists $utilization_file]} {
        foreach line [split [read_text $utilization_file] \n] {
            set cols [split $line |]
            if {[llength $cols] < 7} { continue }
            set name [string trim [lindex $cols 1]]
            if {[regexp {^(Slice LUTs|CLB LUTs|Slice Registers|CLB Registers|Block RAM Tile|DSPs|DSP48E1|MMCME2_ADV|MMCM)(\*|$)} $name]} {
                if {[lsearch -exact $seen_resources $name] >= 0} { continue }
                lappend seen_resources $name
                puts $f "| $name | [string trim [lindex $cols 2]] | [string trim [lindex $cols 5]] | [string trim [lindex $cols 6]] |"
                incr resource_count
            }
        }
    }
    if {$resource_count == 0} { puts $f "\nResources unavailable; see failure and log." }
    puts $f "\n## Timing (latest available stage)\n"
    if {[llength $timing_values] == 3} {
        lassign $timing_values wns tns whs
        puts $f "WNS: $wns ns; TNS: $tns ns; WHS: $whs ns"
    } else { puts $f "WNS/TNS/WHS unavailable." }
    puts $f "\n## Main SRAM mapping\n\n$mainmem_status"
    foreach cell $mainmem_cells { puts $f "- $cell ([get_property REF_NAME $cell])" }
    puts $f "\n## Configuration/BRAM DRC\n\n$bram_status"
    puts $f "\n## JTAG clock routing\n\n$route_override"
    close $f
}

