###############################################################################
# Lab 4 stage 02: power plan, physical-only cells, placement and tie cells.
# Requires the live database produced by lab4_stage01_floorplan_io.tcl.
###############################################################################

# 15: M4/M5 core and SRAM rings.
setDesignMode -bottomRoutingLayer METAL2 -topRoutingLayer METAL6
deselectAll
setAddRingMode -reset
setAddRingMode -avoid_short true -stacked_via_bottom_layer METAL1 -stacked_via_top_layer METAL5
addRing -nets {VDD VSS} -type core_rings -follow core \
    -layer {top METAL5 bottom METAL5 left METAL4 right METAL4} \
    -width 10 -spacing 2 -offset 5 -center 0
selectInst i_mainmem_i_mainmem
addRing -nets {VDD VSS} -type block_rings -around selected \
    -layer {top METAL5 bottom METAL5 left METAL4 right METAL4} \
    -width 4 -spacing 2 -offset 10 -center 0
deselectAll
verify_drc -check_only special -report ../my_soc_top/my_soc_top_pg_drc.rpt
saveDesign ../my_soc_top/backup/my_soc_top_postRings_M4M5.enc

# 16-19: base grid and distributed SRAM connections.
setAddStripeMode -reset
setAddStripeMode -break_at {block_ring outer_ring} -allow_jog none \
    -stacked_via_bottom_layer METAL4 -stacked_via_top_layer METAL5 \
    -orthogonal_only false
addStripe -nets {VDD VSS} -layer METAL4 -direction vertical -width 2 \
    -spacing 1 -set_to_set_distance 20 -start_from left -start_offset 8 \
    -block_ring_bottom_layer_limit METAL4 -block_ring_top_layer_limit METAL5 \
    -padcore_ring_bottom_layer_limit METAL4 -padcore_ring_top_layer_limit METAL5
addStripe -nets {VDD VSS} -layer METAL5 -direction horizontal -width 4 \
    -spacing 2 -set_to_set_distance 30 -start_from top -start_offset 1 \
    -block_ring_bottom_layer_limit METAL4 -block_ring_top_layer_limit METAL5 \
    -padcore_ring_bottom_layer_limit METAL4 -padcore_ring_top_layer_limit METAL5
setSrouteMode -blockPinConnectRingPinCorners true
sroute -nets {VDD VSS} -connect {blockPin} -inst {i_mainmem_i_mainmem} \
    -blockPin useLef -blockPinWidthRange {0.001 1000} \
    -blockPinLayerRange {METAL3 METAL4} -blockPinTarget {blockring} \
    -layerChangeRange {METAL3 METAL5} -allowLayerChange 1 -allowJogging 1 \
    -targetViaLayerRange {METAL3 METAL5} -crossoverViaLayerRange {METAL3 METAL5}
setAddStripeMode -reset
setAddStripeMode -break_at none -allow_jog none \
    -stacked_via_bottom_layer METAL4 -stacked_via_top_layer METAL6 \
    -orthogonal_only false -remove_floating_stripe_over_block false
addStripe -nets {VDD} -layer METAL6 -direction vertical -width 4 -spacing 4 \
    -set_to_set_distance 40 -start_from left -start_offset 14 \
    -area {250 636 804.595 1387.85} \
    -block_ring_bottom_layer_limit METAL4 -block_ring_top_layer_limit METAL5 \
    -padcore_ring_bottom_layer_limit METAL4 -padcore_ring_top_layer_limit METAL5
addStripe -nets {VSS} -layer METAL6 -direction vertical -width 4 -spacing 4 \
    -set_to_set_distance 40 -start_from left -start_offset 22 \
    -area {250 630 804.595 1393.85} \
    -block_ring_bottom_layer_limit METAL4 -block_ring_top_layer_limit METAL5 \
    -padcore_ring_bottom_layer_limit METAL4 -padcore_ring_top_layer_limit METAL5
setAddStripeMode -reset
setAddStripeMode -break_at none -allow_jog none \
    -stacked_via_bottom_layer METAL3 -stacked_via_top_layer METAL6 \
    -orthogonal_only false -split_vias true -remove_floating_stripe_over_block false
addStripe -nets {VDD VSS} -layer METAL5 -direction horizontal -width 4 \
    -spacing 2 -set_to_set_distance 30 -start_from top -start_offset 15.68 \
    -area {230 660 824.595 1365} \
    -block_ring_bottom_layer_limit METAL4 -block_ring_top_layer_limit METAL5 \
    -padcore_ring_bottom_layer_limit METAL4 -padcore_ring_top_layer_limit METAL5
verify_drc -check_only special -report ../my_soc_top/my_soc_top_pg_drc.rpt
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_pg_connectivity.rpt
defOut -routing ../my_soc_top/my_soc_top_postSramPower.def

# 20-22: pad-to-ring and row-rail connectivity.
setSrouteMode -reset
setSrouteMode -padPinSplit {10 0.6}
# Keep the selection on the core-facing M2 windows.  Extending the area beyond
# x/y=3020 also selects the north/east package-facing ports; allPort/allGeom
# then tries to join both windows through the pad's own obstruction.
sroute -nets {VDD VSS} -connect {padPin} -area {80 80 3020 3020} \
    -connectInsideArea -padPinTarget {ring} -padPinPortConnect {allPort allGeom} \
    -padPinLayerRange {METAL2 METAL2} -layerChangeRange {METAL2 METAL5} \
    -allowLayerChange 1 -allowJogging 1 -targetViaLayerRange {METAL2 METAL5} \
    -crossoverViaLayerRange {METAL2 METAL5} -detailed_log
verify_drc -check_only special -report ../my_soc_top/my_soc_top_postCorePadConnect_drc.rpt
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_postCorePadConnect_pg.rpt
saveDesign ../my_soc_top/backup/my_soc_top_postCorePadConnect.enc
saveDesign ../my_soc_top/backup/my_soc_top_preM1_M4M5.enc
sroute -nets {VDD VSS} -connect {corePin} -corePinLayer {METAL1} \
    -corePinTarget {firstAfterRowEnd} -layerChangeRange {METAL1 METAL4} \
    -allowLayerChange 1 -allowJogging 0 -targetViaLayerRange {METAL1 METAL4} \
    -crossoverViaLayerRange {METAL1 METAL4}
verify_drc -check_only special -report ../my_soc_top/my_soc_top_pg_drc.rpt
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_pg_connectivity.rpt
saveDesign ../my_soc_top/backup/my_soc_top_postM1.enc
saveDesign ../my_soc_top/backup/my_soc_top_postPowerPlan.enc
verifyConnectivity -type special -net {VDDPST VSSPST} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_io_pg_connectivity.rpt

if {[info exists ::env(CHIPDESIGN_STOP_AFTER_POWERPLAN)] &&
    $::env(CHIPDESIGN_STOP_AFTER_POWERPLAN) eq "1"} {
  puts "LAB4_STOP_AFTER_POWERPLAN"
  exit
}

# 23: well taps and decaps.
addWellTap -cell TAPCELLBWP7T -cellInterval 60 -checkerBoard -prefix WELLTAP
verifyWellTap -cell TAPCELLBWP7T -rule 30 \
    -report ../my_soc_top/my_soc_top_welltap.rpt
addWellTap -cell DCAP4BWP7T -cellInterval 60 -skipRow 1 -prefix DECAP
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
verify_drc -check_only special -report ../my_soc_top/my_soc_top_postPhysCells_pg_drc.rpt
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_postPhysCells_pg_connectivity.rpt
saveDesign ../my_soc_top/backup/my_soc_top_postPhysCells.enc

# 24-26: placement and two classes of tie cells.
setPlaceMode -place_detail_preroute_as_obs {} -place_detail_use_check_drc true
setPlaceMode -place_global_ignore_scan true -place_global_reorder_scan false
place_opt_design
checkPlace ../my_soc_top/my_soc_top_place_check.rpt -ignoreOutOfCore
saveDesign ../my_soc_top/backup/my_soc_top_postPlace.enc
set tie_supply_tmp [open ../my_soc_top/tie_supply_exclude_tmp.txt w]
puts $tie_supply_tmp {
i_pad_N_vddio/VDDPST
i_pad_N_poc/VDDPST
i_pad_N_vssio/VSSPST
i_pad_S_vddio/VDDPST
i_pad_S_poc/VDDPST
i_pad_S_vssio/VSSPST
i_pad_W_vddio/VDDPST
i_pad_W_poc/VDDPST
i_pad_W_vssio/VSSPST
i_pad_E_vddio/VDDPST
i_pad_E_poc/VDDPST
i_pad_E_vssio/VSSPST
}
close $tie_supply_tmp
setTieHiLoMode -cell {TIEHBWP7T TIELBWP7T} -maxFanout 1 -maxDistance 100
addTieHiLo -cellPin {PDDW0204CDG:I PDDW0204CDG:DS PDDW0204CDG:OEN PDDW0204CDG:PE PDDW0204CDG:IE}
setTieHiLoMode -cell {TIEHBWP7T TIELBWP7T} -maxFanout 8 -maxDistance 50
addTieHiLo -keepExisting true -excludePin ../my_soc_top/tie_supply_exclude_tmp.txt
file delete ../my_soc_top/tie_supply_exclude_tmp.txt
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
refinePlace
checkPlace ../my_soc_top/my_soc_top_postTie_place.rpt -ignoreOutOfCore
saveNetlist ../my_soc_top/my_soc_top_postTie.v -phys
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_postTie_pg.rpt
timeDesign -preCTS -expandedViews -outDir ../my_soc_top/reports/preCTS
saveDesign ../my_soc_top/backup/my_soc_top_postPlace_tieFixed.enc
puts "LAB4_STAGE02_COMPLETE"
source ../scripts/lab4_stage03_cts_route_export.tcl
