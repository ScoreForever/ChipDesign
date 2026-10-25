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
puts $fp {create_delay_corner -name delay -library_set libs -rc_corner rc}
puts $fp "create_constraint_mode -name func -sdc_files [list [list $sdc]]"
puts $fp {create_analysis_view -name func_tt_io -constraint_mode func -delay_corner delay}
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
source $here/legacy/init_pnr_standard.tcl
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
set signal_ports {}
foreach spec $pads {
    lassign $spec name master port direction side x y orient
    if {$side eq "N"} {set y [expr {$y+$die_side_um-$requested_die}]}
    if {$side eq "E"} {set x [expr {$x+$die_side_um-$requested_die}]}
    addInst -cell $master -inst $name -loc [list $x $y] -ori $orient -place_status fixed -dontSnapToPlacementGrid
    if {$direction ne "supply"} {
        lappend signal_ports $port
        set core_net [get_object_name [get_nets -of_objects [get_ports $port]]]
        detachTerm my_soc_top $port
        addNet ${port}_pad
        attachTerm my_soc_top $port ${port}_pad
        set external [get_object_name [get_nets -of_objects [get_ports $port]]]
        attachTerm $name PAD $external
        if {$direction eq "input"} {
            attachTerm $name C $core_net
            foreach {pin bit} {OEN 1 IE 1 PE 0 DS 0 I 0} {attachTerm $name $pin 1'b$bit}
        } else {
            attachTerm $name I $core_net
            foreach {pin bit} {OEN 0 IE 0 PE 0 DS 0} {attachTerm $name $pin 1'b$bit}
        }
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
globalNetConnect VDDPST -type pgpin -pin VDDPST -all
globalNetConnect VSSPST -type pgpin -pin VSSPST -all
set macro_insts [dbGet -p2 top.insts.cell.name RA1SHD_2048x32M8]
if {[llength $macro_insts]!=1 || $macro_insts eq "0x0"} {error "Expected exactly one mapped SRAM"}
set macro_name [dbGet $macro_insts.name]
lassign $macro_box mx my mw mh
placeInstance $macro_name $mx $my R0 -fixed
addHaloToBlock $halo_um $halo_um $halo_um $halo_um $macro_name -snapToSite
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
