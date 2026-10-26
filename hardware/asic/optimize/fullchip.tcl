# Independent ABI-2 full-chip flow. All geometry is generated in config.tcl.
source config.tcl
set here [file dirname [file normalize [info script]]]
setMultiCpuUsage -localCpu $threads
set init_top_cell my_soc_top
set init_verilog $netlist
set init_lef_file [list $std_lef $io_lef $io_ant_lef $bond_lef $macro_lef $macro_ant_lef]
set fp [open mmmc.tcl w]
puts $fp "create_library_set -name libs -timing [list [list $std_lib $io_lib $macro_lib]]"
puts $fp "create_rc_corner -name rc -temperature 25 -cap_table [list $captable]"
# Corner name matches the course scripts (init_pnr_standard.tcl sets the
# CCOpt primary corner to tt_1p80v_25c).
puts $fp {create_delay_corner -name tt_1p80v_25c -library_set libs -rc_corner rc}
puts $fp "create_constraint_mode -name func -sdc_files [list [list $sdc]]"
puts $fp {create_analysis_view -name func_tt_io -constraint_mode func -delay_corner tt_1p80v_25c}
puts $fp {set_analysis_view -setup {func_tt_io} -hold {func_tt_io}}
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
source $here/legacy/user_define.tcl
source $here/legacy/global_define.tcl
set pnr_cpus $threads
# Legacy globals reuse std_lib, io_lib and macro_gds. Restore the explicit
# immutable run inputs after obtaining the course cell/optimization settings.
source $here/legacy/init_pnr_standard.tcl
source config.tcl
# Keep checkpoint restore references on the real run inputs, not legacy paths.
set init_verilog $netlist
set init_lef_file [list $std_lef $io_lef $io_ant_lef $bond_lef $macro_lef $macro_ant_lef]
set init_mmmc_file [file normalize mmmc.tcl]
set init_top_cell my_soc_top
setDesignMode -bottomRoutingLayer METAL2 -topRoutingLayer METAL6
setDelayCalMode -SIAware true
setOptMode -fixHoldAllowSetupTnsDegrade false -holdTargetSlack 0.02
if {$congestion} {
    setOptMode -checkRoutingCongestion true -honorDensityScreenInOpt true
    setPlaceMode -place_global_cong_effort high
}
set requested_die $die_side_um
floorPlan -site core7T -s $core_side_um $core_side_um $margin_um $margin_um $margin_um $margin_um
# Use actual snapped die geometry, not the requested dimensions.
lassign [lindex [dbGet top.fPlan.box] 0] dx0 dy0 dx1 dy1
set die_side_um [expr {$dx1-$dx0}]
set edge [expr {$die_side_um-$corner_um}]
foreach spec [list [list bottom_left 0 0 R0] [list bottom_right $edge 0 MY] [list top_left 0 $edge MX] [list top_right $edge $edge R180]] {
    lassign $spec name x y orient
    addInst -physical -cell PCORNER -inst corner_$name -loc [list $x $y] -ori $orient -place_status fixed -dontSnapToPlacementGrid
}
source $here/connect_pad.tcl
set signal_ports {}
foreach spec $pads {
    lassign $spec name master port direction side x y orient
    if {$side eq "N"} {set y [expr {$y+$die_side_um-$requested_die}]}
    if {$side eq "E"} {set x [expr {$x+$die_side_um-$requested_die}]}
    addInst -cell $master -inst $name -loc [list $x $y] -ori $orient -place_status fixed -dontSnapToPlacementGrid
    if {$direction ne "supply"} {
        lappend signal_ports $port
        connect_signal_pad $port $name $direction
    }
    # Separate wire-bond opening is physical only, as in the course template.
    set bx $x;set by $y
    if {$side eq "N"} {set by [expr {$die_side_um-$bond_depth_um}]}
    if {$side eq "E"} {set bx [expr {$die_side_um-$bond_depth_um}]}
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
# macros: list of {instance x y w h}, matched by role so each store keeps its box.
set macros {}
foreach box $macro_boxes {
    lassign $box role mx my mw mh
    set pattern [expr {$role eq "model" ? "*model_store*" : "*activation*"}]
    set match {}
    foreach inst $macro_insts {if {[string match $pattern [dbGet $inst.name]]} {lappend match [dbGet $inst.name]}}
    if {[llength $match]!=1} {error "Cannot identify the $role SRAM instance: $match"}
    set name [lindex $match 0]
    placeInstance $name $mx $my R0 -fixed
    addHaloToBlock $halo_um $halo_um $halo_um $halo_um $name -snapToSite
    lappend macros [list $name $mx $my $mw $mh]
}
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
source $here/route.tcl
