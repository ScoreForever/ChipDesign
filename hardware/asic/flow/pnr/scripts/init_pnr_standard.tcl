#####################################################################################
# Description:  Innovus Physical Implementation with Standard Speed
# Modifier:     Mingxuan Li <mingxuanli_siris@163.com> [Peking University]
# Acknowledge:  Columbia University, System Level Design Group
#####################################################################################

setMultiCpuUsage -localCpu $pnr_cpus

# change the process technology dependent default settings globally
# congEffort:       possible value: low     | medium    | high      | auto
# flowEffort:       possible value: express | standard  | extreme
# powerEffort:      possible value: none    | low       | high
setDesignMode   -process            180 \
                -congEffort         auto \
                -earlyClockFlow     false \
                -expressRoute       false \
                -flowEffort         standard \
                -powerEffort        low \
                -bottomRoutingLayer ${bottomRoutingLayer} \
                -topRoutingLayer    ${topRoutingLayer}

# global analysis modes for timing analysis
# analysisType: possible value: single | bcwc | onChipVariation
# aocv:         advanced on-chip variation analysis
# cppr:         clock path pessimism removal (highly recommended), possible value: none | both | setup | hold
# usefulSkew:   consider the latency file (latency_file.sdc) during timing analysis, the latency file is generated when you run "skewClock" command
setAnalysisMode -analysisType   onChipVariation \
                -aocv           false \
                -cppr           both \
                -usefulSkew     true

# specify any required delay calculation mode settings before running any commands that perform delay calculation, such as timing analysis and optimization
# accuracy_level:               possible value: 0 | 1 | 2 | 3 | 4
# advanced_pincap_mode:         simulates the current stage and receiver stages together, such that the back miller effect is modeled dynamically
# equivalent_waveform_model:    impact of input waveform shape is considered during delay calculation and the waveforms are propagated across each stage of the entire path, possible value: none | no_propagation | propagation
# SIAware:                      enable SIAware delay calculation that also include cross-talk induced delays
setDelayCalMode -accuracy_level             0 \
                -advanced_pincap_mode       false \
                -equivalent_waveform_model  propagation \
                -SIAware                    false

# set global parameters for timing optimization
# allEndPoints:                     run total negative slack optimization in addition to the worst negative slack optimization and does so on all end points to ensure a high quality timing result
# checkRoutingCongestion:           check the routing congestion at the PostRoute stage during optimization of setup, hold, drv, or glitch the option also impacts postCTS hold fixing, possible value: auto | false | true
# fixFanoutLoad:                    force timing optimization to correct fanout load violations, which can be maximum load or maximum fanout count violations
# fixHoldAllowOverlap:              allow hold-fixing algorithm to insert buffers with overlap when large enough empty space locations are not available, possible value: auto | true | false
# fixHoldAllowResizing:             control the resizing capability inside hold optimization, possible value: auto | true | false
# fixHoldAllowSetupTnsDegrade:      control whether the software allows total negative slack to degrade during hold fixing operations
# fixHoldOnExcludedClockNets:       control whether hold fixing will be performed on excluded clock nets or not
# fixSISlew:                        enable fixing SI induced slew violations in postRoute optimization
# highEffortOptCells:               specify the list of cells to be used for high effort optimization, applicable in both pre-route and post-route setup optimization
# holdTargetSlack:                  specify a target slack value in nanoseconds to use for hold analysis only
# honorDensityScreenInOpt:          control whether or not density screens (partial placement blockages used to control congestion) are honored while optimizing timing
# honorFence:                       specify that the timing optimization takes fences or region constraints into account
# leakageToDynamicRatio:            control the priority of the power-driven optimization, "1" for purely leakage-power driven optimization, possible value: [0-1]
# maxDensity:                       specify the maximum value for density (area utilization), when reaching a density close to the value specified by this parameter, "optDesign" has limited optimization capability
# multiBitFlopOpt:                  enable the multi-bit flip flop flow, this flow is called as a part of the preCTS optimization stage, possible value: true | false | mergeOnly | splitOnly
# opt_enable_podv2_clock_opt_flow:  when set to false, enable the `place_opt_design` and `ccopt_design` flow, by default it is set to true, which means the `place_opt_design` V2 (PODV2 or pod_turbo) and `clock_opt_design` flow is enabled 
# postRouteAreaReclaim:             safely reclaim area while not impacting the timing and DRV violations, possible value: none | setupAware | holdAndSetupAware
# postRouteSetupRecovery:           control the Setup Recovery step performed when "optDesign -postRoute" and "optDesign -postRoute -hold" commands have been specified, possible value: false | auto | true
# powerEffort:                      "high" for top priority, possible value: none | low | high
# reclaimArea:                      control whether timing optimization creates additional space by downsizing gates or deleting buffers, while maintaining worst slack and total negative slack during preRoute optimization, possible value: true | false | default
# restruct:                         control whether timing optimization swaps pins and restructures the netlist
# setupTargetSlackForReclaim:       prevent reclaim optimization from creating or working on near critical paths
# setupTargetSlack:                 specify a target slack value in nanoseconds for setup analysis
# timeDesignNumPaths:               allow you to select the number of paths that should be reported per path group during "timeDesign" or "optDesign" final summary reports
# unfixClkInstForOpt:               control whether the software is allowed to resize fixed sequential elements and to move them during placement legalization
# usefulSkew:                       control whether timing optimization runs the "skewClock" command
# usefulSkewCCOpt:                  specify the effort for useful skew in both CTS and postCTS flow, possible value: none | standard | extreme
setOptMode  -allEndPoints                       true \
            -checkRoutingCongestion             false \
            -fixFanoutLoad                      true \
            -fixHoldAllowOverlap                auto \
            -fixHoldAllowResizing               true \
		    -fixHoldAllowSetupTnsDegrade        true \
            -fixHoldOnExcludedClockNets         true \
            -fixSISlew                          true \
            -highEffortOptCells                 ${hold_fixing_cells} \
            -holdFixingCells                    ${hold_fixing_cells} \
		    -holdTargetSlack                    0 \
            -honorDensityScreenInOpt            false \
            -honorFence                         true \
            -leakageToDynamicRatio              1 \
            -maxDensity                         0.75 \
            -multiBitFlopOpt                    false \
            -postRouteAreaReclaim               none \
            -postRouteSetupRecovery             auto \
            -powerEffort                        none \
            -reclaimArea                        default \
            -restruct                           true \
            -setupTargetSlackForReclaim         0 \
            -setupTargetSlack                   0 \
            -timeDesignNumPaths                 50 \
            -unfixClkInstForOpt                 true \
            -usefulSkew                         true \
            -usefulSkewCCOpt                    extreme \
            -usefulSkewPostRoute                true \
            -usefulSkewPreCTS                   true \
            -verbose                            true

# control certain aspects of how the software places cells, affect "place_design" "refinePlace"
# place_design_floorplan_mode:                      used for prototyping and runs quickly to gauge the feasibility of the netlist, default: false
# place_design_refine_place:                        specify whether to run detailed placement (refinePlace) to legalize placed standard cells, default: true
# place_detail_activity_power_driven:               turn on the activity-driven tweakage, default: false
# place_detail_check_cut_spacing:                   enable the via-to-via spacing check during "refinePlace" and "checkPlace", default: false
# place_detail_check_route:                         consider pre-routed FIXED signal wires and avoids creating DRC violations between FIXED signal wires and instance pins, default: false
# place_detail_color_aware_legal:                   enable placer to honor and fix double pattern constraint violations between adjacent cells, default: false
# place_detail_eco_max_distance:                    specify max distance, in microns, in the horizontal or vertical direction for the refinePlace ECO mode, default: 0, minimum: 0, maximum: 9999
# place_detail_irdrop_aware_effort:                 select the IR drop optimization effort level to use during "refinePlace", possible value: none | low | medium | high (not recommended), default: none
# place_detail_legalization_inst_gap:               specify the minimum sites gap between instances, default: 0
# place_detail_use_check_drc:                       enable the FGC engine inside placement for DRC checking, default: false
# place_detail_use_no_diffusion_one_site_filler:    preserve enough spacing for later filler insertion, default: false
# place_global_activity_power_driven:               identify and constrain power-critical nets to reduce switching power, default: false
# place_global_activity_power_driven_effort:        possible value: low | standard | high, default: standard
# place_global_clock_power_driven:                  improve clock wirelength and subsequently clock power by adding more weight to clock nets between clock gates and their respective Flops, default: true
# place_global_clock_power_driven_effort:           possible value: low | standard  | high, default: low
# place_global_cong_effort:                         possible value: low | medium    | high | auto, default: auto
# palce_global_max_density:                         set the maximum density of core area so that the placement engine satisfies the maximum density constraint while working on timing, wire length, and congestion, default: -1
# place_global_place_io_pins:                       move placed and unplaced I/O pins, based on the placement of connected instances in an attempt to find a better I/O pin placement than the one specified in the I/O pin placement or floorplan file, default: false
# place_global_timing_effort:                       possbile value: medium | high, default: medium
# place_global_fast_cts:                            default: false
setPlaceMode    -place_design_floorplan_mode                    false \
                -place_design_refine_place                      true \
                -place_detail_activity_power_driven             false \
                -place_detail_check_cut_spacing                 false \
                -place_detail_check_route                       false \
                -place_detail_color_aware_legal                 false \
                -place_detail_eco_max_distance                  0 \
                -place_detail_irdrop_aware_effort               none \
                -place_detail_legalization_inst_gap             2 \
                -place_detail_use_check_drc                     false \
                -place_detail_use_no_diffusion_one_site_filler  false \
                -place_global_activity_power_driven             false \
                -place_global_activity_power_driven_effort      standard \
                -place_global_clock_power_driven                true \
                -place_global_clock_power_driven_effort         low \
                -place_global_cong_effort                       auto \
                -place_global_max_density                       -1 \
                -place_global_place_io_pins                     false \
                -place_global_timing_effort                     medium \
                -place_global_fast_cts                          false

# set global parameters for "skewClock" command
# maxAllowedDelay:  limit the amount of slack (in nanoseconds) that the software can borrow from neighboring flip-flops when performing useful skew operations
# noBoundary:       exclude or include boundary sequential cells in addition to ordinary sequential elements in useful skew calculations
setUsefulSkewMode   -maxAllowedDelay    [expr ${rm_clock_period}*0.1] \
                    -noBoundary         false \
                    -useCells           $rm_clock_delay_cell

# set signal integrity configuration parameters used for crosstalk analysis and repair
setSIMode   -enable_logical_correlation                 true \
            -enable_glitch_propagation                  true \
            -enable_double_clocking_check               true \
            -individual_attacker_simulation_filtering   true

# set global variables for block rings and core rings
# avoid_short:      specify whether to prevent a ring or a ring segment from being created if it would cause a short violation to occur
setAddRingMode  -avoid_short true


# control certain aspect of how software adds or deletes tie-high and tie-low cells
# cells:        specify the pairs of tie cells to be used
# maxFanout:    specify the number of tie-pins a tie-net can drive, a value of 0 implies no fanout constraint, default: 0
setTieHiLoMode  -cell       $rm_tie_hi_lo_list \
                -maxFanout  8

# set the values of various CCOpt object properties
# update_io_latency:                        determine whether to update IO latencies within `ccopt_design`, default: true
# force_update_io_latency:                  if false, I/O latencies are updated if and only if all SDC clocks are in ideal mode; if true, run I/O latency updates even when clocks are in propagated mode, default: false
# enable_all_views_for_io_latency_update:   enable all views before updating IO latencies and restore the set of enabled views after, default: true
# max_fanout:                               maximum fanout at any point in the clock tree, default: 100, minimum: 2, maximum: 1000
# effort:                                   possible value: high | medium | low, default: medium
# primary_delay_corner:                     specify the delay corner in which clock tree balancing applies the slew and insertion delay targets
set_ccopt_property  update_io_latency                       true
set_ccopt_property  force_update_io_latency                 true
set_ccopt_property  enable_all_views_for_io_latency_update  true
set_ccopt_property  max_fanout                              ${rm_cts_max_fanout}
set_ccopt_property  effort                                  high
set_ccopt_property  buffer_cells                            ${rm_clock_buf_cap_cell}
set_ccopt_property  inverter_cells                          ${rm_clock_inv_cap_cell}
set_ccopt_property  clock_gating_cells                      ${rm_clock_icg_cell}
set_ccopt_property  primary_delay_corner                    tt_1p80v_25c

# set global parameters for clock concurrent optimization
# this command is obsolete, but still work in this release
# cts_use_min_max_path_delay:   when set to true, specify the use of Liberty min_clock_tree_path and max_clock_tree_path delays on clock pins as part of the skew computation; when set to false, Liberty min_clock_tree_path and max_clock_tree_path delays on clock pins are not included as part of the skew computation, default: false
# cts_target_slew:              specify the clock tree transition time target in .lib time units
# cts_target_skew:              specify the target skew in .lib time units
# modify_clock_latency:         pass the latency modification SDCs back to Innovus, default: true
set_ccopt_mode  -cts_use_min_max_path_delay     false \
                -cts_target_slew                $rm_max_clock_transition \
                -cts_target_skew                0.15

# control certain aspects of how the NanoRoute router routes the design, affect the behavior of the following commands: `detailRoute`, `globalRoute`, `globalDetailRoute`, `routeDesign`
# drouteFixAntenna:                 repair process antenna violations by jumping layers, default: true
# drouteUseMultiCutViaEffort:       specify the effort level toward increasing the ratio of double-cut vias to single-cut vias concurrently with routing, possible value: low | medium | high, default: low
# routeAntennaCellName:             specify antenna diode cells to use during optimization
# routeDesignRouteClockNetsFirst:   detect whether clock nets require ECO routing after an `routeDesign`, default: true
# routeInsertAntennaDiode:          insert and place antenna diode cells if there are available placement locations, default: false
# routeReserveSpaceForMultiCut:     reserve space to insert multicut vias in postroute stage, default: true for 32nm designs and below
# routeWidthLithoDriven:            avoid lithography problems during routing, default: false
# routeWithTimingDriven:            minimize timing violations, default: false
setNanoRouteMode    -drouteFixAntenna               true \
                    -drouteUseMultiCutViaEffort     low \
                    -routeAntennaCellName           ${antenna_cell} \
                    -routeDesignRouteClockNetsFirst true \
                    -routeInsertAntennaDiode        true \
                    -routeReserveSpaceForMultiCut   false \
                    -routeWithLithoDriven           false \
                    -routeWithTimingDriven          true

# control certain aspects of how the software adds filler cells, affect the behavior of `addFiller`, `deleteFiller`
# diffCellViol: check violations between cell types, use this feature will increase the runtime of `addFiller`, default: false
# ecoMode:      resolve overlaps of standard cells with fillers and DRC violations of filler geometries, default: false
# fitGap:       fill a gap between cells by adding a combination of cells, instead of by adding the largest cell that fits, if doing so avoid leaving an unfilled single-width gap, default: true
# minHole:      call `verifyGeometry` to check for minimum hole violations before adding filler cells and repairs the violations when `addFiller` runs, default: false
# scheme:       specify the filler insertion scheme, possible value: locationFirst | cellFirst, default: locationFirst
#               locationFirst: fill each given location with the best filler before proceeding to next location
#               cellFirst: insert filler cells based on the user-specified list, the tool tries to insert the first filler from the user list in all possible locations before moving to the second cell in the list
setFillerMode   -diffCellViol   true \
                -ecoMode        true \
                -fitGap         true \
                -minHole        true \
                -scheme         locationFirst

# global parameter setting
set_global timing_aocv_analysis_mode combine_launch_capture
set_global timing_set_clock_source_to_output_as_data true

set timing_aocv_use_cell_depth_for_net false
set timing_disable_lib_pulsewidth_checks false
