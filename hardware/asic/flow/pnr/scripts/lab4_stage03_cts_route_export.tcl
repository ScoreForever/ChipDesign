###############################################################################
# Lab 4 stage 03: CTS, route, signoff-style Innovus checks and final exports.
# Requires the live database produced by stage 02.
###############################################################################

# 27-29: CTS and post-CTS hold optimization.
# Innovus 21.16 does not automatically treat a clock entering through the
# bidirectional PDDW0204CDG PAD as a CTS root.  Keep the external nets intact,
# but place the two physical clock definitions on the core-facing C pins.
set_interactive_constraint_modes [all_constraint_modes -active]
create_clock -period 20 -waveform {0 10} -name core_clk [get_pins i_pad_clk/C]
create_clock -period 100 -waveform {0 50} -name jtag_clk [get_pins i_pad_tck/C]
set_clock_groups -asynchronous -group {core_clk} \
    -group {jtag_clk virtual_jtag_tck} -allow_paths
set_interactive_constraint_modes {}
set_ccopt_property primary_delay_corner delay_tt_io
set_ccopt_property update_io_latency false
set_ccopt_property force_update_io_latency false
create_ccopt_clock_tree_spec -views {func_tt_io}
set_interactive_constraint_modes [all_constraint_modes -active]
set_clock_uncertainty -setup 0.47 [get_clocks {core_clk jtag_clk}]
set_clock_uncertainty -hold 0.2 [get_clocks {core_clk jtag_clk}]
set_input_transition 0.5 [get_ports {clk_i tck_i}]
set_interactive_constraint_modes {}
set_ccopt_property source_output_max_trans 0.5 -clock_tree core_clk
set_ccopt_property source_output_max_trans 0.5 -clock_tree jtag_clk
set_ccopt_property source_max_capacitance 5.0 -clock_tree core_clk
set_ccopt_property source_max_capacitance 5.0 -clock_tree jtag_clk
ccopt_design -check_prerequisites
saveDesign ../my_soc_top/backup/my_soc_top_preCTS.enc
ccopt_design
saveDesign ../my_soc_top/backup/my_soc_top_postCTS_raw.enc
set_interactive_constraint_modes [all_constraint_modes -active]
set_global timing_io_use_clock_network_latency always
set_propagated_clock [get_clocks {core_clk jtag_clk}]
set_interactive_constraint_modes {}
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
checkPlace ../my_soc_top/my_soc_top_postCTS_place_check.rpt -ignoreOutOfCore
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_postCTS_pg_connectivity.rpt
timeDesign -postCTS -expandedViews -outDir ../my_soc_top/reports/postCTS
timeDesign -postCTS -hold -expandedViews -outDir ../my_soc_top/reports/postCTS
saveDesign ../my_soc_top/backup/my_soc_top_preHoldFix.enc
setOptMode -fixHoldAllowSetupTnsDegrade false
optDesign -postCTS -hold -expandedViews -outDir ../my_soc_top/reports/postCTS_holdFix
timeDesign -postCTS -expandedViews -outDir ../my_soc_top/reports/postCTS_holdFix_check
timeDesign -postCTS -hold -expandedViews -outDir ../my_soc_top/reports/postCTS_holdFix_check
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
checkPlace ../my_soc_top/my_soc_top_postHoldFix_place.rpt -ignoreOutOfCore
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_postHoldFix_pg.rpt
saveDesign ../my_soc_top/backup/my_soc_top_postCTS_holdResidual.enc

# 30-32: detailed routing, physical checks, and post-route timing repair.
setAttribute -net VDDPST -skip_routing true
setAttribute -net VSSPST -skip_routing true
setDesignMode -bottomRoutingLayer METAL2 -topRoutingLayer METAL6
setNanoRouteMode -routeWithTimingDriven true -routeDesignRouteClockNetsFirst true
setExtractRCMode -engine postRoute -effortLevel low -tQuantusForPostRoute false
routeDesign
saveDesign ../my_soc_top/backup/my_soc_top_postRoute_raw.enc
verify_drc -check_only default -limit 100000 \
    -report ../my_soc_top/my_soc_top_postRoute_drc.rpt
verifyConnectivity -type regular -error 100000 \
    -report ../my_soc_top/my_soc_top_postRoute_signal_connectivity.rpt
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_postRoute_pg.rpt
verifyProcessAntenna -report ../my_soc_top/my_soc_top_postRoute_antenna.rpt

# The course's validated run required local rerouting of these three nets.
setNanoRouteMode -droutePostRouteSpreadWire false -drouteFixAntenna true
setNanoRouteMode -reset -drouteEndIteration
deselectAll
verifyProcessAntenna -report ../my_soc_top/my_soc_top_postAntennaFix_antenna.rpt
verify_drc -check_only default -limit 100000 \
    -report ../my_soc_top/my_soc_top_postAntennaFix_drc.rpt
verifyConnectivity -type regular -error 100000 \
    -report ../my_soc_top/my_soc_top_postAntennaFix_signal.rpt
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_postAntennaFix_pg.rpt
saveDesign ../my_soc_top/backup/my_soc_top_postRoute_antennaFixed.enc
timeDesign -postRoute -expandedViews -outDir ../my_soc_top/reports/postRoute
timeDesign -postRoute -hold -expandedViews -outDir ../my_soc_top/reports/postRoute
saveDesign ../my_soc_top/backup/my_soc_top_prePostRouteHoldFix.enc
setOptMode -fixHoldAllowSetupTnsDegrade false -holdTargetSlack 0
optDesign -postRoute -hold -expandedViews -outDir ../my_soc_top/reports/postRoute_holdFix
setOptMode -fixHoldAllowSetupTnsDegrade false -holdTargetSlack 0.02
optDesign -postRoute -hold -expandedViews -outDir ../my_soc_top/reports/postRoute_holdAuto
timeDesign -postRoute -expandedViews -outDir ../my_soc_top/reports/postRoute_holdAuto_check
timeDesign -postRoute -hold -expandedViews -outDir ../my_soc_top/reports/postRoute_holdAuto_check
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
checkPlace ../my_soc_top/my_soc_top_postHoldAuto_place.rpt -ignoreOutOfCore
verify_drc -check_only default -limit 100000 \
    -report ../my_soc_top/my_soc_top_postHoldAuto_drc.rpt
verifyConnectivity -type regular -error 100000 \
    -report ../my_soc_top/my_soc_top_postHoldAuto_signal.rpt
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_postHoldAuto_pg.rpt
verifyProcessAntenna -report ../my_soc_top/my_soc_top_postHoldAuto_antenna.rpt
saveDesign ../my_soc_top/backup/my_soc_top_postRoute_timingFixed.enc

# 33: core filler and final physical/timing checks.
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
setFillerMode -core {FILL64BWP7T FILL32BWP7T FILL16BWP7T FILL8BWP7T FILL4BWP7T FILL2BWP7T FILL1BWP7T} \
    -corePrefix FILLER -createRows false -diffCellViol true -ecoMode true \
    -fitGap true -scheme locationFirst -honorPrerouteAsObs false \
    -check_signal_drc true -add_fillers_with_drc false
addFiller
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
checkPlace ../my_soc_top/my_soc_top_postFiller_place.rpt -ignoreOutOfCore
verifyConnectivity -type special -net {VDD VSS} -allPGPinPort -error 100000 \
    -report ../my_soc_top/my_soc_top_postFiller_pg.rpt
verify_drc -check_only default -limit 100000 \
    -report ../my_soc_top/my_soc_top_postFiller_drc.rpt
timeDesign -postRoute -expandedViews -outDir ../my_soc_top/reports/postFiller
timeDesign -postRoute -hold -expandedViews -outDir ../my_soc_top/reports/postFiller
verifyProcessAntenna -report ../my_soc_top/my_soc_top_postFiller_antenna.rpt
verifyConnectivity -type regular -error 100000 \
    -report ../my_soc_top/my_soc_top_postFiller_signal.rpt
saveDesign ../my_soc_top/backup/my_soc_top_postFiller.enc

# 34: all Innovus handoff products.
file mkdir ../my_soc_top/outputs/manual_final
saveDesign ../my_soc_top/backup/my_soc_top_postPinLabels.enc
defOut -routing ../my_soc_top/outputs/manual_final/my_soc_top.def
saveNetlist ../my_soc_top/outputs/manual_final/my_soc_top_flat_lvs.v \
    -excludeLeafCell -flat -phys \
    -excludeCellInst {FILL1BWP7T FILL2BWP7T FILL4BWP7T FILL8BWP7T FILL16BWP7T FILL32BWP7T FILL64BWP7T TAPCELLBWP7T PCORNER PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005 PRCUT PAD60LU_OBV}
saveNetlist ../my_soc_top/outputs/manual_final/my_soc_top_hier_postpnr.v \
    -excludeLeafCell -topModuleFirst -removePowerGround \
    -excludeCellInst {FILL1BWP7T FILL2BWP7T FILL4BWP7T FILL8BWP7T FILL16BWP7T FILL32BWP7T FILL64BWP7T TAPCELLBWP7T PCORNER PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005 PRCUT PAD60LU_OBV DCAPBWP7T DCAP4BWP7T DCAP8BWP7T DCAP16BWP7T DCAP32BWP7T DCAP64BWP7T}
# This Innovus release does not emit an alias assignment when a module port is
# attached to a differently named PAD-side net.  Normalize the six external
# net identifiers in both electrical netlists so their module ports are not
# left floating in simulation or LVS.
proc normalize_pad_port_nets {path} {
    set fh [open $path r]
    set data [read $fh]
    close $fh
    foreach port {clk_i rst_ni tck_i tms_i td_i td_o} {
        regsub -all -- "${port}_pad" $data $port data
    }
    set fh [open $path w]
    puts -nonewline $fh $data
    close $fh
}
normalize_pad_port_nets ../my_soc_top/outputs/manual_final/my_soc_top_flat_lvs.v
normalize_pad_port_nets ../my_soc_top/outputs/manual_final/my_soc_top_hier_postpnr.v
write_sdc -view func_tt_io > ../my_soc_top/outputs/manual_final/my_soc_top.sdc
write_sdc -path_adjust_only > ../my_soc_top/outputs/manual_final/my_soc_top_path_adjust.tcl
if {![dbGet top.statusRCExtracted]} {extractRC}
rcOut -view func_tt_io -cUnit pF -spef \
    ../my_soc_top/outputs/manual_final/my_soc_top_tt_io.spef.gz
write_sdf -min_view func_tt_io -typ_view func_tt_io -max_view func_tt_io \
    -recompute_delay_calc ../my_soc_top/outputs/manual_final/my_soc_top_tt_io.sdf.gz
write_lef_abstract -5.8 -cutObsMinSpacing -specifyTopLayer METAL6 \
    ../my_soc_top/outputs/manual_final/my_soc_top.lef
setStreamOutMode -labelAllPinShape true -virtualConnection false
streamOut ../my_soc_top/outputs/manual_final/my_soc_top.gds.gz \
    -mapFile $rm_lef_layer_map -merge $gds_list -mode ALL -units 2000
puts "LAB4_STAGE03_COMPLETE"
exit
