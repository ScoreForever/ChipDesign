# INT4 full-chip flow with legacy and ABI-5 shared-bank role mapping. All geometry is generated in config.tcl.
source config.tcl
set here $opt_dir
source $here/sram_roles.tcl
setMultiCpuUsage -localCpu $threads
if {[info exists ::env(TAPEOUT_RESUME_ROUTED)] && $::env(TAPEOUT_RESUME_ROUTED) eq "1" && [file exists routed.enc.dat]} {
    puts "INFO: Resuming strict 1980 um flow from routed.enc.dat"
    restoreDesign routed.enc.dat my_soc_top
    source config.tcl
    source $here/cell_stats.tcl
    source $here/npu_jtag_cdc.tcl
    lassign [lindex [dbGet top.fPlan.box] 0] dx0 dy0 dx1 dy1
    set die_w_um [expr {$dx1-$dx0}]
    set die_h_um [expr {$dy1-$dy0}]
    set die_side_um $die_h_um
    set signal_ports {}
    foreach spec $pads {
        lassign $spec name master port direction side x y orient
        if {$direction ne "supply"} {lappend signal_ports $port}
    }
    set prects_txt [read [set _pfp [open prects_cells.json r]]]; close $_pfp
    regexp {"active_standard_instances":\s*([0-9]+)} $prects_txt _ _pi
    regexp {"active_standard_area_um2":\s*([0-9.eE+-]+)} $prects_txt _ _pa
    regexp {"macro_count":\s*([0-9]+)} $prects_txt _ _mc
    regexp {"macro_area_um2":\s*([0-9.eE+-]+)} $prects_txt _ _ma
    set prects_stats [list $_pi $_pa $_mc $_ma]
} else {
set init_top_cell my_soc_top
set init_verilog $netlist
set init_lef_file [list $std_lef $io_lef $io_ant_lef $bond_lef $macro_lef $macro_ant_lef]
set fp [open mmmc.tcl w]
puts $fp "create_constraint_mode -name func -sdc_files [list [list $sdc]]"
foreach {corner suffix macro temp dc} {
    ss125 wc ss_1p62_125C 125 ss_1p62v_125c
    tt25  tc tt_1p8_25C    25 tt_1p80v_25c
    ff0   bc ff_1p98_0C     0 ff_1p98v_0c
    ffm40 lt ff_1p98_-40C -40 ff_1p98v_m40c
} {
 puts $fp "create_library_set -name libs_$corner -timing [list [list $std_dir/tcb018gbwp7t${suffix}.lib $io_dir/tpd018nv${suffix}.lib $sram_dir/RA1SHD_2048x32M8_${macro}_syn.lib]]"
 puts $fp "create_rc_corner -name rc_$corner -temperature $temp -cap_table [list $captable]"
 puts $fp "create_delay_corner -name $dc -library_set libs_$corner -rc_corner rc_$corner"
 puts $fp "create_analysis_view -name view_$corner -constraint_mode func -delay_corner $dc"
}
foreach {rc view corner scale temp} [list ss125_p15 ss125_rcp15 ss125 $rc_setup_scale 125 ss125_p20 ss125_rcp20 ss125 1.20 125 ffm40_m15 ffm40_rcm15 ffm40 0.85 -40 ffm40_m20 ffm40_rcm20 ffm40 $rc_hold_scale -40] {
 puts $fp "create_rc_corner -name rc_$rc -temperature $temp -cap_table [list $captable] -postRoute_res $scale -postRoute_cap $scale -postRoute_xcap $scale"
 puts $fp "create_delay_corner -name dc_$rc -library_set libs_$corner -rc_corner rc_$rc"
 puts $fp "create_analysis_view -name view_$view -constraint_mode func -delay_corner dc_$rc"
}
puts $fp {set_analysis_view -setup {view_ss125 view_tt25} -hold {view_ffm40 view_ff0 view_tt25 view_ss125}}
close $fp
set init_mmmc_file [file normalize mmmc.tcl]
set init_pwr_net VDD
set init_gnd_net VSS
setLibraryUnit -cap 1pf -time 1ns
init_design
set pnr_cpus $threads
set std_lib tcb018gbwp7t
set cell_ext BWP7T
set rm_core_top my_soc_top
# Load cell lists only; paths below remain the external course library paths.
set ::env(CHIPDESIGN_PROJECT_ROOT) $source_root
source $flow_dir/config/user_define.tcl
source $flow_dir/config/global_define.tcl
set pnr_cpus $threads
# Legacy globals reuse std_lib, io_lib and macro_gds. Restore the explicit
# immutable run inputs after obtaining the course cell/optimization settings.
source $flow_dir/pnr/scripts/init_pnr_standard.tcl
source config.tcl
# Keep checkpoint restore references on the real run inputs, not legacy paths.
set init_verilog $netlist
set init_lef_file [list $std_lef $io_lef $io_ant_lef $bond_lef $macro_lef $macro_ant_lef]
set init_mmmc_file [file normalize mmmc.tcl]
set init_top_cell my_soc_top
setDesignMode -bottomRoutingLayer METAL2 -topRoutingLayer METAL6
setAnalysisMode -analysisType onChipVariation -cppr both
setDelayCalMode -SIAware true
if {![info exists hold_target]} {set hold_target 0.02}
setOptMode -fixHoldAllowSetupTnsDegrade false -holdTargetSlack $hold_target
# The course default caps optimization at 75% density; above that hold fixing
# stops inserting buffers and leaves violations (seen at 70% utilization).
setOptMode -maxDensity 0.9
if {$congestion} {
    setOptMode -checkRoutingCongestion true -honorDensityScreenInOpt true
    setPlaceMode -place_global_cong_effort high
}
set requested_die_w $die_w_um
set requested_die_h $die_h_um
floorPlan -site core7T -s $core_w_um $core_h_um $margin_x_um $margin_y_um $margin_x_um $margin_y_um
# Use actual snapped die geometry, not the requested dimensions.
lassign [lindex [dbGet top.fPlan.box] 0] dx0 dy0 dx1 dy1
set die_w_um [expr {$dx1-$dx0}]
set die_h_um [expr {$dy1-$dy0}]
if {$die_w_um > $ref_inner_um+1e-6 || $die_h_um > $ref_inner_um+1e-6} {error "Die exceeds sealring reference"}
set die_side_um $die_h_um
set edge_x [expr {$die_w_um-$corner_um}]
set edge_y [expr {$die_h_um-$corner_um}]
foreach spec [list [list bottom_left 0 0 R0] [list bottom_right $edge_x 0 MY] [list top_left 0 $edge_y MX] [list top_right $edge_x $edge_y R180]] {
    lassign $spec name x y orient
    addInst -physical -cell PCORNER -inst corner_$name -loc [list $x $y] -ori $orient -place_status fixed -dontSnapToPlacementGrid
}
source $here/connect_pad.tcl
set signal_ports {}
foreach spec $pads {
    lassign $spec name master port direction side x y orient
    if {$side eq "N"} {set y [expr {$y+$die_h_um-$requested_die_h}]}
    if {$side eq "E"} {set x [expr {$x+$die_w_um-$requested_die_w}]}
    addInst -cell $master -inst $name -loc [list $x $y] -ori $orient -place_status fixed -dontSnapToPlacementGrid
    if {$direction ne "supply"} {
        lappend signal_ports $port
        connect_signal_pad $port $name $direction
    }
    # Separate wire-bond opening is physical only, as in the course template.
    set bx $x;set by $y
    if {$side eq "N"} {set by [expr {$die_h_um-$bond_depth_um}]}
    if {$side eq "E"} {set bx [expr {$die_w_um-$bond_depth_um}]}
    addInst -physical -cell PAD60LU_OBV -inst bond_$name -loc [list $bx $by] -ori $orient -place_status fixed -dontSnapToPlacementGrid
}
source $here/register_io.tcl
addNet VDDPST -power
addNet VSSPST -ground
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
foreach side {N S W E} {
    attachTerm i_pad_${side}_vddio VDDPST VDDPST
    attachTerm i_pad_${side}_poc VDDPST VDDPST
    attachTerm i_pad_${side}_vssio VSSPST VSSPST
}
foreach {side ori} {top R180 bottom R0 left R270 right R90} {
    addIoFiller -cell {PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005} -prefix IOF_$side -side $side -fillerOrient $ori
}
# IO cells expose VDDPST/VSSPST only through abutting rails, not as LEF
# power pins; the supply PADs are bound with attachTerm above. A global
# connection here matches nothing and raises IMPDB-1221.
set macro_insts [dbGet -p2 top.insts.cell.name RA1SHD_2048x32M8]
if {[llength $macro_insts]!=2 || $macro_insts eq "0x0"} {error "Expected exactly two mapped SRAMs (model and activation)"}
# macros: list of {instance x y w h orient}, matched by role so each store keeps
# its box; w x h is the placed footprint and older configs default to R0.
set macros {}
set macro_ptrs [dict create]
foreach box $macro_boxes {
    lassign $box role mx my mw mh orient
    if {$orient eq ""} {set orient R0}
    set match {}
    foreach inst $macro_insts {
        set inst_name [npu_sram_name [dbGet $inst.name]]
        dict set macro_ptrs $inst_name $inst
        if {[npu_sram_role $inst_name] eq $role} {lappend match $inst_name}
    }
    if {[llength $match]!=1} {error "Cannot identify the $role SRAM instance: $match"}
    set name [lindex $match 0]
    placeInstance $name $mx $my $orient -fixed
    addHaloToBlock $halo_um $halo_um $halo_um $halo_um $name -snapToSite
    lappend macros [list $name $mx $my $mw $mh $orient]
}
# Which side the D/Q bus actually faces: mean D/Q pin position relative to the
# placed box. flow.py compares it with the side the orientation should give.
set pin_report [open macro_pins.tsv w]
foreach m $macros {
    lassign $m name mx my mw mh orient
    set sx 0.0; set sy 0.0; set n 0
    set macro_ptr [dict get $macro_ptrs $name]
    foreach term [dbGet $macro_ptr.instTerms] {
        # dbGet returns lists; a bus bit such as D[0] comes back brace-quoted.
        if {![string match {[DQ]\[*} [lindex [dbGet $term.cellTerm.name] 0]]} {continue}
        lassign [lindex [dbGet $term.pt] 0] px py
        set sx [expr {$sx+$px}]; set sy [expr {$sy+$py}]; incr n
    }
    if {$n == 0} {error "No D/Q pins found on $name"}
    set dx [expr {($sx/$n-($mx+$mw/2.0))/$mw}]; set dy [expr {($sy/$n-($my+$mh/2.0))/$mh}]
    if {abs($dx) > abs($dy)} {set side [expr {$dx > 0 ? "E" : "W"}]} else {set side [expr {$dy > 0 ? "N" : "S"}]}
    puts $pin_report "$name\t$orient\t$side\t$n\t[format %.3f $dx]\t[format %.3f $dy]"
}
close $pin_report
# Label ports on package-facing PAD metal, including irq_o.
foreach spec $pads {
    lassign $spec name master port direction side x y orient
    if {$port eq "-"} {continue}
    if {$side eq "N"} {set py [expr {$die_side_um-2}]} else {set py 2}
    editPin -pin $port -layer METAL4 -assign [list [expr {$x+30}] $py] -pinWidth 0.4 -pinDepth 0.4 -fixedPin -snap USERGRID
}
set bond_supply_offset 10
foreach spec $pads {
    lassign $spec name master port direction side x y orient
    if {$side ne "N" || $direction ne "supply"} {continue}
    set net ""
    if {[string match *_vdd_1 $name]} {set net VDD}
    if {[string match *_vss_1 $name]} {set net VSS}
    if {[string match *_vddio $name]} {set net VDDPST}
    if {[string match *_vssio $name]} {set net VSSPST}
    if {$net ne ""} {createPGPin $net -net $net -geom METAL4 [expr {$x+10}] [expr {$die_side_um-2}] [expr {$x+20}] [expr {$die_side_um-1}]}
}
saveDesign floorplan.enc
source $here/power_place.tcl
source $here/cell_stats.tcl
set prects_stats [capture_cell_stats prects]
# Rebuild physical clock roots at PAD core pins without changing frequencies.
set_interactive_constraint_modes [all_constraint_modes -active]
create_clock -period 20 -name core_clk [get_pins i_pad_clk_i/C]
create_clock -period 1000 -name jtag_clk [get_pins i_pad_tck_i/C]
source $here/npu_jtag_cdc.tcl
npu_cdc::apply
set_clock_uncertainty 0.2 [get_clocks {core_clk jtag_clk}]
set_input_transition 0.5 [get_ports {clk_i tck_i}]
set_interactive_constraint_modes {}
set_analysis_view -setup {view_ss125 view_tt25} -hold {view_ffm40 view_ff0 view_tt25 view_ss125}
set_ccopt_property primary_delay_corner ss_1p62v_125c
set_ccopt_property update_io_latency false
set_ccopt_property force_update_io_latency false
create_ccopt_clock_tree_spec -views {view_ss125 view_tt25 view_ff0 view_ffm40}
ccopt_design -check_prerequisites
ccopt_design
set_interactive_constraint_modes [all_constraint_modes -active]
set_propagated_clock [get_clocks {core_clk jtag_clk}]
set_interactive_constraint_modes {}
optDesign -postCTS -setup -outDir postcts_setup
optDesign -postCTS -hold -outDir postcts_hold
saveDesign cts.enc
source $here/wirebond_keepout.tcl
add_wirebond_keepouts [llength $pads]
setAttribute -net VDDPST -skip_routing true
setAttribute -net VSSPST -skip_routing true
setNanoRouteMode -routeWithTimingDriven true -routeDesignRouteClockNetsFirst true -drouteFixAntenna true
setExtractRCMode -engine postRoute -effortLevel low -tQuantusForPostRoute false
set_analysis_view -setup {view_ss125_rcp15 view_ss125 view_tt25} -hold {view_ffm40_rcm20 view_ffm40 view_ff0 view_tt25 view_ss125}
setOptMode -setupTargetSlack $setup_target -holdTargetSlack $hold_target -fixHoldAllowSetupTnsDegrade false -maxDensity 0.90 -usefulSkewPostRoute true
routeDesign
optDesign -postRoute -setup -outDir postroute_setup
optDesign -postRoute -hold -outDir postroute_hold
optDesign -postRoute -setup -incremental -outDir postroute_setup_inc
optDesign -postRoute -hold -outDir postroute_hold_inc
saveDesign routed.enc
}
if {[catch {addFiller -cell {DCAP16BWP7T DCAP8BWP7T DCAP4BWP7T} -prefix DCAP -fitGap true -check_signal_drc true -add_fillers_with_drc false} msg]} {puts "DECAP_SKIPPED: $msg"}
setFillerMode -core {FILL64BWP7T FILL32BWP7T FILL16BWP7T FILL8BWP7T FILL4BWP7T FILL2BWP7T FILL1BWP7T} -corePrefix FILLER -createRows false -diffCellViol true -ecoMode true -fitGap true -scheme locationFirst -honorPrerouteAsObs false -check_signal_drc true -add_fillers_with_drc false
addFiller
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
source $here/repair_antenna.tcl
repair_process_antenna
# Floating metal fill on M5/M6, the only layers below the foundry 30% density
# (Calibre M5.R.1/M6.R.1). Added before the final DRC and extraction so both
# see it; timing-aware fill avoids critical nets.
setMetalFill -layer {METAL5 METAL6} -minDensity 30 -preferredDensity 35 -maxDensity 70 -windowSize 200 200 -windowStep 100 100
addMetalFill -layer {METAL5 METAL6} -timingAware sta
# Remove dangling special-wire stubs (stripe ends past the matching ring);
# they connect nothing and fail the strict PG connectivity check.
editTrim -nets {VDD VSS}
source $here/repair_drc.tcl
repair_signal_drc
checkPlace placement.rpt -ignoreOutOfCore
verify_drc -check_only default -limit 100000 -report drc.rpt
verify_drc -check_only special -limit 100000 -report pg_drc.rpt
verifyConnectivity -type regular -error 100000 -report connectivity.rpt
verifyConnectivity -type special -net {VDD VSS VDDPST VSSPST} -allPGPinPort -error 100000 -report pg_connectivity.rpt
verifyProcessAntenna -report antenna.rpt
timeDesign -postRoute -expandedViews -outDir timing_setup
timeDesign -postRoute -hold -expandedViews -outDir timing_hold
report_timing -late -max_paths 100 > timing_setup.rpt
report_timing -early -max_paths 100 > timing_hold.rpt
report_constraint -all_violators > constraints.rpt
npu_cdc::audit cdc_paths.tsv
report_area > area.rpt
reportCongestion -overflow > congestion.rpt
extractRC
proc worst_path {mode args} {
    set col [report_timing -collection $mode -max_paths 1 {*}$args]
    if {[sizeof_collection $col]==0} {return {NA NA NA NA NA}}
    set row {}
    foreach prop {slack launching_point capturing_point arrival required} {
        set v [get_property $col $prop]
        if {$prop in {launching_point capturing_point}} {
            if {[catch {set n [get_object_name $v]}] == 0 && $n ne ""} {set v [lindex $n 0]}
        }
        lappend row $v
    }
    return $row
}
proc slack_stats {mode} {
    set paths [report_timing -collection $mode -max_paths 100000]
    set wns 1e30;set tns 0.0;set nviol 0
    foreach_in_collection path $paths {
        set slack [get_property $path slack]
        if {$slack < $wns} {set wns $slack}
        if {$slack < 0} {set tns [expr {$tns+$slack}]; incr nviol}
    }
    if {$wns == 1e30} {error "No timed paths"}
    return [list $wns $tns $nviol]
}
set mmmc_f [open mmmc_corners.csv w]
puts $mmmc_f "corner,mode,wns,start,end,arrival,required,tns,violating_endpoints"
set sram_f [open sram_interface_timing.csv w]
puts $sram_f "corner,instance,role,setup_to,hold_to,setup_from,hold_from"
set cdc_f [open cdc_4corners.tsv w]
puts $cdc_f "corner\tkind\tfrom\tto\tmode\tarrival_ns\trequired_ns\tslack_ns"
foreach corner {ss125 tt25 ff0 ffm40} {
    set_analysis_view -setup [list view_$corner] -hold [list view_$corner]
    foreach {mode label} {-late setup -early hold} {
        lassign [worst_path $mode] pw start end arr req
        lassign [slack_stats $mode] wns tns nviol
        puts $mmmc_f "$corner,$label,$wns,$start,$end,$arr,$req,$tns,$nviol"
        report_timing $mode -max_paths 20 > timing_${label}_${corner}.rpt
        if {$corner eq "tt25" && $mode eq "-late"} {set setup_wns $wns; set setup_tns $tns}
        if {$corner eq "tt25" && $mode eq "-early"} {set hold_wns $wns; set hold_tns $tns}
    }
    foreach inst [dbGet -p2 top.insts.cell.name RA1SHD_2048x32M8] {
        set name [npu_sram_name [dbGet $inst.name]]
        set cell_col [get_cells -hierarchical -filter "full_name == \"$name\""]
        set row [list $corner $name [npu_sram_role $name]]
        foreach direction {-to -from} {
            if {$direction eq "-to"} {
                set pins [get_pins -of_objects $cell_col -filter "direction == in && name != CLK"]
            } else {
                set pins [get_pins -of_objects $cell_col -filter "name == CLK"]
            }
            foreach mode {-late -early} {
                if {[sizeof_collection $pins]==0 || [catch {worst_path $mode $direction $pins} result]} {lappend row NA} else {lappend row [lindex $result 0]}
            }
        }
        puts $sram_f [join $row ,]
    }
    npu_cdc::index_cells
    set specs [npu_cdc::payloads]
    foreach bank {req_sync ack_sync} {
        foreach mode {-late -early} {lappend specs [list $bank [npu_cdc::pin $bank 0 Q] [npu_cdc::pin $bank 1 D] $mode]}
    }
    foreach spec $specs {
        lassign $spec kind from to mode
        if {$mode eq ""} {set mode -late}
        lassign [worst_path $mode -from $from -to $to] slack start end arrival required
        puts $cdc_f "$corner\t$kind\t[lindex [get_object_name $from] 0]\t[lindex [get_object_name $to] 0]\t[string range $mode 1 end]\t$arrival\t$required\t$slack"
    }
}
close $cdc_f; close $sram_f; close $mmmc_f

set rc_f [open rc_sensitivity.tsv w]
puts $rc_f "view\tcorner\trc_scale\tmode\twns_ns\ttns_ns\tviolating_endpoints"
foreach {view corner scale mode label} {
    view_ss125 ss125 1.00 -late setup
    view_ss125_rcp15 ss125 1.15 -late setup
    view_ss125_rcp20 ss125 1.20 -late setup
    view_ffm40 ffm40 1.00 -early hold
    view_ffm40_rcm15 ffm40 0.85 -early hold
    view_ffm40_rcm20 ffm40 0.80 -early hold
} {
    set_analysis_view -setup [list $view] -hold [list $view]
    lassign [slack_stats $mode] wns tns nviol
    puts $rc_f "$view\t$corner\t$scale\t$label\t$wns\t$tns\t$nviol"
}
close $rc_f

set_analysis_view -setup {view_tt25} -hold {view_tt25}
report_timing -late -max_paths 100 > timing_setup.rpt
report_timing -early -max_paths 100 > timing_hold.rpt
set_power_analysis_mode -method static -analysis_view view_tt25
set_default_switching_activity -input_activity 0.1 -seq_activity 0.1
report_power -power_unit mW -outfile power.rpt
set_analysis_view -setup {view_ss125 view_tt25} -hold {view_ffm40 view_ff0 view_tt25 view_ss125}
# Physical checks use raw report text, interpreted by the Python validator.
set final_stats [capture_cell_stats final]
set fp [open metrics.json w]
lassign [lindex [dbGet top.fPlan.coreBox] 0] cx0 cy0 cx1 cy1
set core_area [expr {($cx1-$cx0)*($cy1-$cy0)/1e6}]
puts $fp [format {"die_area_mm2":%.9f,"core_area_mm2":%.9f,"setup_wns_ns":%.9f,"setup_tns_ns":%.9f,"hold_wns_ns":%.9f,"hold_tns_ns":%.9f,"active_standard_instances":%d,"active_standard_area_um2":%.9f,"macro_count":%d,"macro_area_um2":%.9f,"cts_and_repair_area_change_um2":%.9f} [expr {($dx1-$dx0)*($dy1-$dy0)/1e6}] $core_area $setup_wns $setup_tns $hold_wns $hold_tns [lindex $final_stats 0] [lindex $final_stats 1] [lindex $final_stats 2] [lindex $final_stats 3] [expr {[lindex $final_stats 1]-[lindex $prects_stats 1]}]]
close $fp
set fp [open metrics.json r];set data [read $fp];close $fp
set final_stats [capture_cell_stats final]
set fp [open metrics.json w];puts $fp "\173$data\175";close $fp
set fp [open ports.txt w]
puts $fp [get_object_name [get_ports *]]
close $fp
set fp [open io_connectivity.tsv w]
foreach spec $pads {
    lassign $spec name master port direction side x y orient
    if {$port eq "-"} {continue}
    if {$direction eq "input"} {set core_pin C} else {set core_pin I}
    puts $fp "$port [get_object_name [get_nets -of_objects [get_ports $port]]] [get_object_name [get_nets -of_objects [get_pins $name/PAD]]] [get_object_name [get_nets -of_objects [get_pins $name/$core_pin]]] $direction"
}
close $fp
set fp [open io_geometry.tsv w]
foreach inst [dbGet top.insts] {
    set class [dbGet $inst.cell.baseClass]
    set cell [dbGet $inst.cell.name]
    if {$class ni {pad corner} && $cell ne "PAD60LU_OBV"} {continue}
    puts $fp "[dbGet $inst.name] $cell [dbGet $inst.orient] [lindex [dbGet $inst.box] 0] $die_side_um"
}
close $fp
saveDesign final.enc
file mkdir outputs
saveNetlist outputs/physical.v -excludeLeafCell -flat -phys -excludeCellInst {FILL1BWP7T FILL2BWP7T FILL4BWP7T FILL8BWP7T FILL16BWP7T FILL32BWP7T FILL64BWP7T TAPCELLBWP7T PCORNER PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005 PRCUT PAD60LU_OBV}
# Hierarchical simulation netlist as in the course flow (Lab4 step 34): -phys
# implies power/ground and cannot be combined with -removePowerGround.
# Antenna diodes stay: they have a Verilog model and appear in the SDF.
saveNetlist outputs/simulation.v -excludeLeafCell -topModuleFirst -removePowerGround -excludeCellInst {FILL1BWP7T FILL2BWP7T FILL4BWP7T FILL8BWP7T FILL16BWP7T FILL32BWP7T FILL64BWP7T TAPCELLBWP7T PCORNER PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005 PRCUT PAD60LU_OBV DCAPBWP7T DCAP4BWP7T DCAP8BWP7T DCAP16BWP7T DCAP32BWP7T DCAP64BWP7T}
foreach path {outputs/simulation.v outputs/physical.v} {
    set fp [open $path r];set text [read $fp];close $fp
    foreach port $signal_ports {regsub -all -- "${port}_pad" $text $port text}
    set fp [open $path w];puts -nonewline $fp $text;close $fp
}
write_sdc -view view_tt25 > outputs/mapped.sdc
extractRC
rcOut -view view_tt25 -cUnit pF -spef outputs/routed.spef.gz
write_sdf -min_view view_ffm40 -typ_view view_tt25 -max_view view_ss125 -recompute_delay_calc outputs/routed.sdf.gz
defOut -routing outputs/routed.def
write_lef_abstract -5.8 -cutObsMinSpacing -specifyTopLayer METAL6 outputs/chip.lef
setStreamOutMode -labelAllPinShape true -virtualConnection false
streamOut outputs/chip.gds.gz -mapFile $gds_map -merge [list $std_gds $io_gds $bond_gds $macro_gds] -mode ALL -units 2000
file mkdir backup
saveDesign backup/final.enc
file mkdir lab4_exports
foreach {src dst} {physical.v my_soc_top_flat_lvs.v simulation.v my_soc_top_hier_postpnr.v mapped.sdc my_soc_top.sdc routed.def my_soc_top.def routed.spef.gz my_soc_top.spef.gz chip.lef my_soc_top.lef chip.gds.gz my_soc_top.gds.gz} {file copy -force outputs/$src lab4_exports/$dst}
file copy -force $opt_dir/npu_jtag_cdc.tcl lab4_exports/my_soc_top_path_adjust.tcl
write_sdc -view view_ss125 > outputs/mapped_ss125.sdc
write_sdf -min_view view_ffm40 -typ_view view_tt25 -max_view view_ss125 lab4_exports/my_soc_top.sdf.gz
foreach {corner label} {ss125 max ffm40 min} {
    set_analysis_view -setup [list view_$corner] -hold [list view_$corner]
    write_sdf -min_view view_$corner -typ_view view_$corner -max_view view_$corner -recompute_delay_calc lab4_exports/my_soc_top_${corner}_${label}.sdf.gz
}
set_analysis_view -setup {view_ss125 view_tt25} -hold {view_ffm40 view_ff0 view_tt25 view_ss125}
puts STAGE_TAPEOUT_1980_COMPLETE
exit

