# Rebuild physical clock roots at PAD core pins without changing frequencies.
set_interactive_constraint_modes [all_constraint_modes -active]
create_clock -period 20 -name core_clk [get_pins i_pad_clk_i/C]
create_clock -period 1000 -name jtag_clk [get_pins i_pad_tck_i/C]
set_clock_groups -asynchronous -group core_clk -group jtag_clk
set_clock_uncertainty 0.2 [get_clocks {core_clk jtag_clk}]
set_input_transition 0.5 [get_ports {clk_i tck_i}]
set_interactive_constraint_modes {}
set_ccopt_property primary_delay_corner delay
set_ccopt_property update_io_latency false
set_ccopt_property force_update_io_latency false
create_ccopt_clock_tree_spec -views {func_tt_io}
ccopt_design -check_prerequisites
ccopt_design
set_interactive_constraint_modes [all_constraint_modes -active]
set_propagated_clock [get_clocks {core_clk jtag_clk}]
set_interactive_constraint_modes {}
optDesign -postCTS -hold -outDir postcts
saveDesign cts.enc
setAttribute -net VDDPST -skip_routing true
setAttribute -net VSSPST -skip_routing true
setNanoRouteMode -routeWithTimingDriven true -routeDesignRouteClockNetsFirst true -drouteFixAntenna true
setExtractRCMode -engine postRoute -effortLevel low -tQuantusForPostRoute false
routeDesign
optDesign -postRoute -hold -outDir postroute_hold
saveDesign routed.enc
setFillerMode -core {FILL64BWP7T FILL32BWP7T FILL16BWP7T FILL8BWP7T FILL4BWP7T FILL2BWP7T FILL1BWP7T} -corePrefix FILLER -createRows false -diffCellViol true -ecoMode true -fitGap true -scheme locationFirst -honorPrerouteAsObs false -check_signal_drc true -add_fillers_with_drc false
addFiller
globalNetConnect VDD -type pgpin -pin VDD -all
globalNetConnect VSS -type pgpin -pin VSS -all
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
report_area > area.rpt
reportCongestion -overflow > congestion.rpt
set_power_analysis_mode -method static
set_default_switching_activity -input_activity 0.1 -seq_activity 0.1
report_power -power_unit mW -outfile power.rpt
# Full path collection is needed only to compute negative slack totals.
proc slack_stats {mode} {
    set paths [report_timing -collection $mode -max_paths 100000]
    set wns 1e30;set tns 0
    foreach_in_collection path $paths {
        set slack [get_property $path slack]
        if {$slack < $wns} {set wns $slack}
        if {$slack < 0} {set tns [expr {$tns+$slack}]}
    }
    if {$wns == 1e30} {error "No timed paths"}
    return [list $wns $tns]
}
lassign [slack_stats -late] setup_wns setup_tns
lassign [slack_stats -early] hold_wns hold_tns
# Physical checks use raw report text, interpreted by the Python validator.
set fp [open metrics.json w]
lassign [lindex [dbGet top.fPlan.coreBox] 0] cx0 cy0 cx1 cy1
set core_area [expr {($cx1-$cx0)*($cy1-$cy0)/1e6}]
puts $fp [format {"die_area_mm2":%.9f,"core_area_mm2":%.9f,"setup_wns_ns":%.9f,"setup_tns_ns":%.9f,"hold_wns_ns":%.9f,"hold_tns_ns":%.9f} [expr {($dx1-$dx0)*($dy1-$dy0)/1e6}] $core_area $setup_wns $setup_tns $hold_wns $hold_tns]
close $fp
set fp [open metrics.json r];set data [read $fp];close $fp
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
    if {$class ni {pad corner} || $cell eq "PAD60LU_OBV"} {continue}
    puts $fp "[dbGet $inst.name] $cell [dbGet $inst.orient] [lindex [dbGet $inst.box] 0] $die_side_um"
}
close $fp
saveDesign final.enc
file mkdir outputs
saveNetlist outputs/physical.v -excludeLeafCell -flat -phys
saveNetlist outputs/simulation.v -phys -excludeLeafCell -topModuleFirst -removePowerGround -excludeCellInst {FILL1BWP7T FILL2BWP7T FILL4BWP7T FILL8BWP7T FILL16BWP7T FILL32BWP7T FILL64BWP7T TAPCELLBWP7T PCORNER PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005 PRCUT PAD60LU_OBV DCAPBWP7T DCAP4BWP7T DCAP8BWP7T DCAP16BWP7T DCAP32BWP7T DCAP64BWP7T}
foreach path {outputs/simulation.v outputs/physical.v} {
    set fp [open $path r];set text [read $fp];close $fp
    foreach port $signal_ports {regsub -all -- "${port}_pad" $text $port text}
    set fp [open $path w];puts -nonewline $fp $text;close $fp
}
write_sdc -view func_tt_io > outputs/mapped.sdc
extractRC
rcOut -view func_tt_io -cUnit pF -spef outputs/routed.spef.gz
write_sdf -min_view func_tt_io -typ_view func_tt_io -max_view func_tt_io -recompute_delay_calc outputs/routed.sdf.gz
defOut -routing outputs/routed.def
write_lef_abstract -5.8 -cutObsMinSpacing -specifyTopLayer METAL6 outputs/chip.lef
setStreamOutMode -labelAllPinShape true -virtualConnection false
streamOut outputs/chip.gds.gz -mapFile $gds_map -merge [list $std_gds $io_gds $bond_gds $macro_gds] -mode ALL -units 2000
# True tool-rendered views; retain report failures rather than inventing images.
dumpPictures -dir images -prefix fullchip
saveDesign final.enc
puts "PNR_OPT_PHYSICAL_COMPLETE"
exit
