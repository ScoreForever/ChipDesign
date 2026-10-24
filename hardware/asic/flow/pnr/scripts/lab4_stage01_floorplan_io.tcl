###############################################################################
# Lab 4 stage 01: import, floorplan, IO/pad ring and SRAM placement.
# Run from pnr/logs after Genus has produced the postsynthesis handoff.
###############################################################################

source ../scripts/init_pnr.tcl

file mkdir ../my_soc_top/backup
file mkdir ../my_soc_top/reports

# 02-04: 2800 um core with the course-standard 150 um core-to-die margin.
floorPlan -site core7T -s 2800 2800 150 150 150 150
puts "LAB4_CHECK die=[dbGet top.fPlan.box] core=[dbGet top.fPlan.coreBox]"
saveDesign ../my_soc_top/backup/my_soc_top_prePad.enc

# 05: corner cells.
foreach spec {
    {corner_bottom_left 0 0 R0}
    {corner_bottom_right 2985.16 0 MY}
    {corner_top_left 0 2985.16 MX}
    {corner_top_right 2985.16 2985.16 R180}
} {
    lassign $spec name x y orient
    addInst -physical -cell PCORNER -inst $name -loc [list $x $y] \
        -ori $orient -place_status fixed -dontSnapToPlacementGrid
}

# 06: include the IO timing library and make this the only active TT view.
set io_tt_lib /lec/pdk/tsmc180/tpd018nv_280a/TSMCHOME/digital/Front_End/timing_power_noise/NLDM/tpd018nv_280a/tpd018nvtc.lib
create_library_set -name lib_tt_io -timing [concat $proj(library_set,tt_1p80v_25c) $io_tt_lib]
create_delay_corner -name delay_tt_io -library_set lib_tt_io \
    -rc_corner rc_tt_1p80v_25c
create_analysis_view -name func_tt_io -constraint_mode func \
    -delay_corner delay_tt_io
set_analysis_view -setup {func_tt_io} -hold {func_tt_io}

# 07: six signal pads.
foreach spec {
    {i_pad_clk 300 2985.16 R180}
    {i_pad_rst_n 2600 2985.16 R180}
    {i_pad_tck 250 0 R0}
    {i_pad_tms 450 0 R0}
    {i_pad_tdi 2600 0 R0}
    {i_pad_tdo 2800 0 R0}
} {
    lassign $spec name x y orient
    addInst -cell PDDW0204CDG -inst $name -loc [list $x $y] \
        -ori $orient -place_status fixed -dontSnapToPlacementGrid
}

# 08: split each top-level signal into an external pad net and core net.
foreach spec {
    {clk_i  i_pad_clk   C 1 1 0 0 0}
    {rst_ni i_pad_rst_n C 1 1 0 0 0}
    {tck_i  i_pad_tck   C 1 1 0 0 0}
    {tms_i  i_pad_tms   C 1 1 0 0 0}
    {td_i   i_pad_tdi   C 1 1 0 0 0}
    {td_o   i_pad_tdo   I 0 0 0 0 0}
} {
    lassign $spec port pad core_pin oen ie pe ds input_value
    set ext_net ${port}_pad
    addNet $ext_net
    # Preserve the original core net object before detachModulePort.  Innovus
    # automatically renames that net (for example, clk_i becomes n_1) once the
    # module port is moved to the external pad net.
    set core_net_ptr [dbGet top.nets.name $port -p]
    set core_term_ptr [lindex [dbGet $core_net_ptr.instTerms] 0]
    detachModulePort - $port
    attachModulePort - $port $ext_net
    # Attaching a top-level module port renames its net to the port name.
    # Re-query it before attaching the PAD pin; using the stale ${port}_pad
    # string would silently create a second, disconnected net.
    set ext_net [dbGet [dbGet -p1 top.terms.name $port].net.name]
    set core_net [dbGet $core_term_ptr.net.name]
    attachTerm $pad PAD $ext_net
    attachTerm $pad $core_pin $core_net
    attachTerm $pad OEN 1'b$oen
    attachTerm $pad IE  1'b$ie
    attachTerm $pad PE  1'b$pe
    attachTerm $pad DS  1'b$ds
    if {$core_pin eq "C"} { attachTerm $pad I 1'b$input_value }
}
saveNetlist ../my_soc_top/my_soc_top_io_connected.v
saveDesign ../my_soc_top/backup/my_soc_top_postSignalPad_fixed.enc

# 09: seven supply pads on every side.
set pad_defs {
    {vss_1  PVSS1CDG 900}
    {vss_2  PVSS1CDG 1100}
    {vssio  PVSS2CDG 1300}
    {vdd_1  PVDD1CDG 1500}
    {vdd_2  PVDD1CDG 1700}
    {vddio  PVDD2CDG 1900}
    {poc    PVDD2POC 2100}
}
foreach side_spec {
    {N 2985.16 R180 horizontal}
    {S 0 R0 horizontal}
    {W 0 R270 vertical}
    {E 2985.16 R90 vertical}
} {
    lassign $side_spec side edge orient axis
    foreach pad_spec $pad_defs {
        lassign $pad_spec suffix master t
        if {$axis eq "horizontal"} { set loc [list $t $edge] } \
        else { set loc [list $edge $t] }
        addInst -cell $master -inst i_pad_${side}_${suffix} -loc $loc \
            -ori $orient -place_status fixed -dontSnapToPlacementGrid
    }
}
saveIoFile -locations -includeCellName ../my_soc_top/my_soc_top_io_beforeFiller.io

# Register IO sides from the actual fixed geometry without moving instances.
set io_die [lindex [dbGet top.fPlan.box] 0]
lassign $io_die io_dx0 io_dy0 io_dx1 io_dy1
set io_tol 0.001
set io_rows [dict create top {} bottom {} left {} right {}]
set io_corners [dict create]
set io_before [dict create]
foreach io_inst [dbGet top.insts] {
    set io_class [dbGet $io_inst.cell.baseClass]
    if {$io_class ni {pad corner}} {continue}
    set io_subclass [dbGet $io_inst.cell.subClass]
    if {[string match -nocase *spacer* $io_subclass]} {continue}
    set io_name [dbGet $io_inst.name]
    set io_cell [dbGet $io_inst.cell.name]
    if {[dbGet $io_inst.pStatus] ne "fixed"} {error "unfixed IO: $io_name"}
    set io_box [lindex [dbGet $io_inst.box] 0]
    lassign $io_box io_x0 io_y0 io_x1 io_y1
    set io_ori [dbGet $io_inst.orient]
    dict set io_before $io_name [list $io_box $io_ori]
    if {$io_class eq "corner"} {
        set h ""; set v ""
        if {abs($io_x0-$io_dx0) < $io_tol} {set h left}
        if {abs($io_x1-$io_dx1) < $io_tol} {set h right}
        if {abs($io_y0-$io_dy0) < $io_tol} {set v bottom}
        if {abs($io_y1-$io_dy1) < $io_tol} {set v top}
        if {$h eq "" || $v eq ""} {error "corner not on die: $io_name"}
        dict set io_corners ${v}${h} [list $io_name $io_cell $io_ori]
    } else {
        set distances [lsort -real -index 0 [list \
            [list [expr {abs($io_y0-$io_dy0)}] bottom] \
            [list [expr {abs($io_dy1-$io_y1)}] top] \
            [list [expr {abs($io_x0-$io_dx0)}] left] \
            [list [expr {abs($io_dx1-$io_x1)}] right]]]
        set indent [lindex $distances 0 0]
        set side [lindex $distances 0 1]
        if {$side in {top bottom}} {set offset [expr {$io_x0-$io_dx0}]} \
        else {set offset [expr {$io_y0-$io_dy0}]}
        dict lappend io_rows $side [list $offset $io_name $io_cell $indent $io_ori]
    }
}
if {[dict size $io_corners] != 4} {error "expected four IO corners"}
set io_tmp_path ../my_soc_top/io_registration_tmp.io
set io_fd [open $io_tmp_path w]
puts $io_fd "(globals\n version = 3\n io_order = default\n)\n(iopad"
foreach side {topright top topleft left bottomleft bottom bottomright right} {
    puts $io_fd " ($side"
    if {[dict exists $io_corners $side]} {
        lassign [dict get $io_corners $side] name cell orient
        puts $io_fd [format {  (inst name="%s" cell="%s" orientation=%s place_status=fixed)} $name $cell $orient]
    } else {
        foreach row [lsort -real -index 0 [dict get $io_rows $side]] {
            lassign $row offset name cell indent orient
            puts $io_fd [format {  (inst name="%s" cell="%s" offset=%.6f indent=%.6f orientation=%s place_status=fixed)} $name $cell $offset $indent $orient]
        }
    }
    puts $io_fd " )"
}
puts $io_fd ")"
close $io_fd
loadIoFile $io_tmp_path -noAdjustDieSize -specifiedIosOnly
foreach io_name [dict keys $io_before] {
    set io_inst [dbGet top.insts.name $io_name -p]
    lassign [dict get $io_before $io_name] io_old_box io_old_ori
    set io_new_box [lindex [dbGet $io_inst.box] 0]
    foreach io_old_value $io_old_box io_new_value $io_new_box {
        if {abs($io_old_value-$io_new_value) > $io_tol} {
            error "IO registration moved fixed instance: $io_name"
        }
    }
    if {[dbGet $io_inst.orient] ne $io_old_ori} {
        error "IO registration changed orientation: $io_name"
    }
}
file delete $io_tmp_path
saveIoFile -locations -includeCellName ../my_soc_top/my_soc_top_io_registered.io
defOut ../my_soc_top/my_soc_top_io_registered.def

# 10: 34 package-level wire-bond pads (physical only).
foreach spec {
    {i_bond_clk 300 3027.24 R180} {i_bond_rst_n 2600 3027.24 R180}
    {i_bond_tck 250 0 R0} {i_bond_tms 450 0 R0}
    {i_bond_tdi 2600 0 R0} {i_bond_tdo 2800 0 R0}
} {
    lassign $spec name x y orient
    addInst -physical -cell PAD60LU_OBV -inst $name -loc [list $x $y] \
        -ori $orient -place_status fixed -dontSnapToPlacementGrid
}
foreach side_spec {
    {N 3027.24 R180 horizontal}
    {S 0 R0 horizontal}
    {W 0 R270 vertical}
    {E 3027.24 R90 vertical}
} {
    lassign $side_spec side edge orient axis
    foreach item {{vss_1 900} {vss_2 1100} {vssio 1300} {vdd_1 1500} {vdd_2 1700} {vddio 1900} {poc 2100}} {
        lassign $item suffix t
        if {$axis eq "horizontal"} {set loc [list $t $edge]} else {set loc [list $edge $t]}
        addInst -physical -cell PAD60LU_OBV -inst i_bond_${side}_${suffix} \
            -loc $loc -ori $orient -place_status fixed -dontSnapToPlacementGrid
    }
}
defOut ../my_soc_top/my_soc_top_preIOFiller.def

# 11-12: bind four independent supplies and close the IO ring.
addNet VDDPST -power
addNet VSSPST -ground
globalNetConnect VDD -type pgpin -pin VDD -all -verbose
globalNetConnect VSS -type pgpin -pin VSS -all -verbose
foreach side {N S W E} {
    attachTerm i_pad_${side}_vddio VDDPST VDDPST
    attachTerm i_pad_${side}_poc VDDPST VDDPST
    attachTerm i_pad_${side}_vssio VSSPST VSSPST
}
saveNetlist ../my_soc_top/my_soc_top_pg_bound.v -phys
saveDesign ../my_soc_top/backup/my_soc_top_postPGConnect.enc
addIoFiller -cell {PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005} -prefix IOF_TOP -side top -fillerOrient R180
addIoFiller -cell {PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005} -prefix IOF_BOTTOM -side bottom -fillerOrient R0
addIoFiller -cell {PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005} -prefix IOF_LEFT -side left -fillerOrient R270
addIoFiller -cell {PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005} -prefix IOF_RIGHT -side right -fillerOrient R90
saveDesign ../my_soc_top/backup/my_soc_top_postIOFiller.enc

# 13-14: SRAM and the ten final physical pins.
placeInstance i_mainmem_i_mainmem 250 650 R0 -fixed
addHaloToBlock 26 26 26 26 i_mainmem_i_mainmem -snapToSite
checkFPlan -reportUtil -outFile ../my_soc_top/my_soc_top_floorplan_check.rpt
saveDesign ../my_soc_top/backup/my_soc_top_postMacroPlace.enc
createPGPin VDD -net VDD -geom METAL4 1510 3098 1520 3099
createPGPin VSS -net VSS -geom METAL4 910 3098 920 3099
createPGPin VDDPST -net VDDPST -geom METAL4 1910 3098 1920 3099
createPGPin VSSPST -net VSSPST -geom METAL4 1310 3098 1320 3099
# PDDW0204CDG exposes its package PAD terminal on METAL4 at local
# y=0..5.6 um.  Put each signal port shape wholly inside that terminal so
# GDS stream-out carries both the correct text label and electrical geometry.
foreach signal_pin {
    {clk_i  330 3098}
    {rst_ni 2630 3098}
    {tck_i  280 2}
    {tms_i  480 2}
    {td_i   2630 2}
    {td_o   2830 2}
} {
    lassign $signal_pin pin_name x y
    editPin -pin $pin_name -layer METAL4 -assign [list $x $y] \
        -pinWidth 0.4 -pinDepth 0.4 -fixedPin -snap USERGRID
}
defOut ../my_soc_top/my_soc_top_physicalPins.def
saveDesign ../my_soc_top/backup/my_soc_top_postPhysicalPins.enc
puts "LAB4_STAGE01_COMPLETE"
if {[info exists ::env(CHIPDESIGN_STOP_AFTER_STAGE1)] &&
    $::env(CHIPDESIGN_STOP_AFTER_STAGE1) eq "1"} {
    puts "LAB4_STOP_AFTER_STAGE1"
    exit
}
source ../scripts/lab4_stage02_power_place.tcl
