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

if {[catch {
    cd $out
    set_param general.maxThreads $jobs
    create_project -in_memory -part xc7a35tcsg324-1
    set_property target_language Verilog [current_project]
    set_property simulator_language Mixed [current_project]
    set stage clock_ip
    file mkdir [file join $out ip]
    create_ip -name clk_wiz -vendor xilinx.com -library ip -module_name clk_wiz_0 -dir [file join $out ip]
    set_property -dict [list CONFIG.PRIM_IN_FREQ {100.000} CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $frequency_mhz CONFIG.USE_RESET {false} CONFIG.USE_LOCKED {true}] [get_ips clk_wiz_0]
    generate_target all [get_ips clk_wiz_0]
    synth_ip [get_ips clk_wiz_0]
    set stage read_sources
    set sources {}
    set includes {}
    foreach line [split [read_text [file join $repo hardware/soc/sim/filelists/chipdesign_nocpu.f]] \n] {
        set line [string trim $line]
        if {$line eq "" || [string match "#*" $line] || [string match "//*" $line]} { continue }
        if {[string match "+incdir+*" $line]} {
            lappend includes [file join $repo [string range $line 8 end]]
        } elseif {[string index $line 0] eq "+"} {
            if {![string match "*.svh" $line]} { error "Unsupported filelist directive: $line" }
        } elseif {![string match "hardware/soc/sim/*" $line]} {
            lappend sources [file join $repo $line]
        }
    }
    set_property include_dirs $includes [current_fileset]
    set_property verilog_define [list SYNTHESIS CHIPDESIGN_FPGA_MEM NPU_ARRAY_COLS=$array_cols] [current_fileset]
    read_verilog -sv $sources
    read_verilog -sv [file join $repo hardware/fpga/fpga_top.sv]
    read_xdc [file join $repo hardware/fpga/ego1_pins.xdc]
    read_xdc [file join $repo hardware/fpga/ego1_timing.xdc]
    read_xdc [file join $repo hardware/fpga/ego1_configuration.xdc]
    set stage synth
    synth_design -top fpga_top -part xc7a35tcsg324-1
    # This filelist has the dedicated NPU TAP, not the CPU DMI mailbox.
    source [file join $repo hardware/fpga/scripts/constrain_npu_jtag_cdc.tcl]
    constrain_npu_jtag_cdc
    audit_npu_jtag_cdc [file join $out synth_npu_jtag_cdc.tsv]
    write_checkpoint -force [file join $out synth.dcp]
    reports synth
    bram_guard synth
    set last_stage synth
    if {$synth_only} {
        set result PASS_SYNTHESIS_ONLY
        set stage complete
        set failure ""
        summary
        puts "PASS EGo1 synthesis only; no implementation/bitstream; summary: [file join $out summary.md]"
        exit 0
    }
    set stage opt
    opt_design
    write_checkpoint -force [file join $out opt.dcp]
    reports opt
    bram_guard opt
    set last_stage opt
    set stage place
    if {[catch {place_design} place_error place_options]} {
        # Retry only the documented non-dedicated JTAG clock pin failure.
        set place_log [read_text [file join $out vivado.log]]
        if {![regexp {ERROR: \[Place 30-574\]} $place_log] || ![string match "*tck_i*" $place_log]} {
            return -options $place_options $place_error
        }
        close_design
        open_checkpoint [file join $out opt.dcp]
        set tck_net [get_nets -of_objects [get_ports tck_i]]
        if {[llength $tck_net] != 1} { error "Cannot identify the JTAG clock input net" }
        set_property CLOCK_DEDICATED_ROUTE FALSE $tck_net
        set route_override "CLOCK_DEDICATED_ROUTE FALSE on $tck_net after Place 30-574 (E15 is not a dedicated clock pin)"
        place_design
    }
    write_checkpoint -force [file join $out place.dcp]
    reports place
    set last_stage place
    set stage route
    # Match implement_nocpu.tcl: preserve the clock/I/O/CDC requirements and
    # improve placement before routing. The full build must use the same
    # physical flow as the source-verified checkpoint implementation helper.
    phys_opt_design
    route_design
    set initial_wns [get_property SLACK [lindex [get_timing_paths -delay_type max -max_paths 1] 0]]
    if {$initial_wns < 0} {
        phys_opt_design -directive AggressiveExplore
        route_design -directive Explore
    }
    audit_npu_jtag_cdc [file join $out route_npu_jtag_cdc.tsv]
    source [file join $repo hardware/fpga/scripts/audit_jtag_io.tcl]
    audit_jtag_io [file join $out io_audit]
    write_checkpoint -force [file join $out route.dcp]
    reports route
    report_timing -delay_type max -max_paths 50 -nworst 1 -input_pins -nets -file [file join $out route_setup_paths.rpt]
    report_timing -delay_type min -max_paths 20 -nworst 1 -input_pins -nets -file [file join $out route_hold_paths.rpt]
    report_high_fanout_nets -max_nets 50 -file [file join $out route_high_fanout.rpt]
    report_cdc -details -file [file join $out route_cdc.rpt]
    report_clock_interaction -file [file join $out route_clock_interaction.rpt]
    bram_guard route
    if {$ppa_mode} {
        # Actual MMCM clock period, rather than the requested wizard frequency.
        set soc_clock [get_clocks -of_objects [get_pins u_clk_wiz/clk_out1]]
        if {[llength $soc_clock] != 1} { error "Expected one SoC clock" }
        set metrics [open [file join $out route_metrics.tsv] w]
        puts $metrics "requested_mhz	$frequency_mhz"
        puts $metrics "period_ns	[get_property PERIOD $soc_clock]"
        puts $metrics "wns_ns	[lindex $timing_values 0]"
        puts $metrics "whs_ns	[lindex $timing_values 2]"
        close $metrics
    }
    set last_stage route
    set stage write_bitstream
    write_bitstream -force [file join $out fpga_top.bit]
    set last_stage write_bitstream
    set stage acceptance
    if {[llength $timing_values] != 3} { error "No timing metrics available" }
    lassign $timing_values wns tns whs
    if {$wns < 0 || $whs < 0} { error "Timing failed: WNS=$wns ns, WHS=$whs ns" }
    # Implemented DRCs and write_bitstream gate resource overflow and routing.
    set result PASS
    set stage complete
} failure options]} {
    puts stderr "ERROR: EGo1 build failed at $stage: $failure; log: [file join $out vivado.log]"
    puts stderr [dict get $options -errorinfo]
    summary
    exit 1
}
set failure ""
summary
puts "PASS EGo1 build; summary: [file join $out summary.md]"
exit 0
