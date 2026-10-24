#####################################################################################
# Description:  Global Definition File for Synthesis and Physical Implementation
# Modifier:     Mingxuan Li <mingxuanli_siris@163.com> [Peking University]
# Acknowledge:  Columbia University, System Level Design Group
#####################################################################################

# -----------------------------------------------------------------------------------
# Design Specific Library Setup
# -----------------------------------------------------------------------------------

set rm_foundry_lib_dirs     /lec/pdk/tsmc180/tcb018gbwp7t_290a/TSMCHOME/digital 
set base_lib_dir            ${rm_foundry_lib_dirs}/Front_End/timing_power_noise/NLDM/${std_lib}_270a
set base_gds_dir            ${rm_foundry_lib_dirs}/Back_End/gds/${std_lib}_270a

# Library parameters
# PVT corners of std cells
set std_tt_1p80v_25c_opcond  tc


# -----------------------------------------------------------------------------------
# Path to library directories
# -----------------------------------------------------------------------------------
set rm_macro_dirs                   ../../src/macro

set rm_captable_file ${rm_foundry_lib_dirs}/Back_End/lef/tcb018gbwp7t_270a/techfiles/captable/t018lo_1p6m_typical.captable

# Provide files for INNOVUS LEF Tech files
set rm_lef_layer_map           ${rm_foundry_lib_dirs}/Back_End/lef/tcb018gbwp7t_270a/techfiles/gds2.map



# -----------------------------------------------------------------------------------
# Technology Library Setup
# -----------------------------------------------------------------------------------
# Path for Lef libs
set rm_lef_reflib [concat ${rm_foundry_lib_dirs}/Back_End/lef/${std_lib}_270a/lef/${std_lib}_6lm.lef \
		/lec/pdk/tsmc180/tpd018nv_280a/TSMCHOME/digital/Back_End/lef/tpd018nv_280a/mt_2/6lm/lef/tpd018nv_6lm.lef \
		/lec/pdk/tsmc180/tpd018nv_280a/TSMCHOME/digital/Back_End/lef/tpd018nv_280a/mt_2/6lm/lef/antenna_6lm.lef \
		/lec/pdk/tsmc180/tpb018v_180a/TSMCHOME/digital/Back_End/lef/tpb018v_180a/cup/6lm/lef/tpb018v_6lm.lef \
		]

# Add memory lef libraries
# Add macro lef libraries
#foreach macro ${macro_insts} { \
#  set rm_lef_reflib [concat $rm_lef_reflib ${rm_macro_dirs}/${macro}/${macro}.vclef] \
#}
foreach macro ${macro_insts} { \
    set rm_lef_reflib [concat $rm_lef_reflib \
	${rm_macro_dirs}/${macro}/${macro}.vclef \
     	${rm_macro_dirs}/${macro}/${macro}_ant.lef ]\
}

# Set this multiplier (eg. 2) to the desired width and spacing of the clock-tree routing
set rm_clock_routing_width_multiplier   2
set rm_clock_routing_spacing_multiplier 2

# -----------------------------------------------------------------------------------
# Cell Specific Setup
# The numerical values in the [] brackets in the associated comment are some reasonable examples the user may adopt
# -----------------------------------------------------------------------------------
# This section contains lists of cells which are used, or excluded, by specific parts of the implementation flow

# Tie cells used to provide logic-1 and logic-0. Variable used in ICC flow steps
set rm_tie_hi_lo_list [list TIEH${cell_ext} \
                            TIEL${cell_ext} ]

# Output pin on the above-mentioned tie cells
set rm_tie_cell_pin     "Z"


# These lists contains all the cells available for use by Clock Tree Synthesis (CTS)
# CTS only uses one Vt for better path matching across a die. These lists are
# referenced only during clock_opt

foreach cell $cell_ext { 
  set rm_clock_buf_cap_cell [concat CKBD0${cell} CKBD10${cell} CKBD12${cell} CKBD1${cell} CKBD2${cell} CKBD3${cell} CKBD4${cell} CKBD6${cell} CKBD8${cell}]
}

foreach cell $cell_ext { 
  set rm_clock_inv_cap_cell [concat INVD0${cell} INVD10${cell} INVD12${cell} INVD1${cell} INVD1P5${cell} INVD2${cell} INVD2P5${cell} INVD3${cell} INVD4${cell} INVD5${cell} INVD6${cell} INVD8${cell}]
}

#PREICG  - active high scan-enable
set rm_clock_icg_cell [list CKLNQD1${cell_ext}  CKLNQD2${cell_ext}  CKLNQD4${cell_ext} CKLNQD6${cell_ext} CKLNQD8${cell_ext} \
			                CKLHQD1${cell_ext}  CKLHQD2${cell_ext} \
			                CKLHQD4${cell_ext} CKLHQD6${cell_ext} CKLHQD8${cell_ext}]

# If delay cells are needed they are referenced from this list
set rm_logic_delay_cell [list   DEL015${cell_ext} DEL01${cell_ext} DEL02${cell_ext} DEL0${cell_ext} \
				DEL1${cell_ext} DEL2${cell_ext} DEL3${cell_ext} DEL4${cell_ext} ]
set rm_clock_delay_cell [list   BUFFD0${cell_ext} BUFFD10${cell_ext} BUFFD12${cell_ext} BUFFD1${cell_ext} BUFFD1P5${cell_ext} BUFFD2${cell_ext} BUFFD2P5${cell_ext} BUFFD3${cell_ext} BUFFD4${cell_ext} \
 				BUFFD5${cell_ext} BUFFD6${cell_ext} BUFFD8${cell_ext} ]

# List of hold-fixing delay cells. User may use the list in $rm_logic_delay_cell in combination with other delay or buffer cells
set hold_fixing_cells [concat $rm_logic_delay_cell $rm_clock_delay_cell ]

# Filler cells used for density compliance
set rm_fill_cells [list FILL1${cell_ext}   FILL2${cell_ext} DCAP4${cell_ext} DCAP8${cell_ext} \
                        DCAP16${cell_ext}  DCAP32${cell_ext} DCAP64${cell_ext} ]

## Tap cell to be used in design planning
set rm_tap_cell TAPCELL${cell_ext}

## Distance in um between Tap cells
set rm_tap_cell_distance   60	;# maximum distance of 66um
## Offset between Tap cells
set rm_tap_cell_offset     0	;# [0]

## Endcap cells
set dcap_cell       [list DCAP4${cell_ext}]

## Antenna diode cells
set antenna_cell    [list ANTENNA${cell_ext}]

## I/O filler & PAD cells
set IO_fill_cell    [list PCORNER PFILLER20 PFILLER10 PFILLER5 PFILLER1 PFILLER05 PFILLER0005 PRCUT]
set PAD_cell        [list PAD60LU_OBV]

## List of physical only cells to be excluded from the LVS netlist. This list would typically include tap, filler, antenna and boundary cells
set lvs_exclude_cells [concat ${rm_tap_cell} ${rm_fill_cells} ${IO_fill_cell} ${PAD_cell}]

# -----------------------------------------------------------------------------------
# Design Clock Period 
# The numerical values in the [] brackets in the associated comment are some reasonable examples the user may adopt
# -----------------------------------------------------------------------------------
set rm_max_transition                   0.3    ;# maximum rise/fall signal transition time in ns [0.250]
set rm_max_clock_transition             0.5     ;# maximum rise/fall clock transition time in ns [0.150]
set rm_max_pad_transition               0.6     ;# maximum rise/fall pad transition time in ns [1.00]
set rm_clock_latency                    0.2     ;# Predicted clock insertion delay in ns [1.00]
set rm_icg_latency                      0.14    ;# Latency in ns for integrated clock gating cell [0.07]
set rm_rz_setup_margin                  0.1    ;# in ns. Setup margin [0.05]
set rm_setup_margin                     0.1    ;# in ns. Setup margin [0.05]
set rm_hold_margin                      0.2     ;# in ns. Hold margin [0.075]
set rm_clock_uncertainty                0.2    ;# in ns. Pre-CTS clock skew estimate [0.1]
set rm_period_jitter                    0.02    ;# Cycle jitter (rise-to-rise) +/- N ns [0.03]
set rm_pre_cts_clock_uncertainty        0.4     ;# in ns. Pre-CTS clock skew estimate [0.1]
set rm_post_cts_clock_uncertainty       0.35   ;# in ns. Post-CTS clock skew estimate [0.075]

# ---------------------------------------------------------------------------------------------------
# Parameters used in Timing Characterization
# The numerical values in the [] brackets in the associated comment are some reasonable examples the user may adopt
# ---------------------------------------------------------------------------------------------------

set rm_load_value                       0.2                 ;# Capacitive load in pF placed on all outputs [0.04]
set rm_driving_cell                     BUFFD4${cell_ext}   ;# The driving cell for all inputs
set rm_driving_pin                      "Z"                 ;# The output pin of the driving cell
set rm_clock_driving_cell               BUFFD4${cell_ext}   ;# The driving cell for all inputs
set rm_clock_driving_pin                "Z"                 ;# The output pin of the clock driving cell
set rm_dcd_jitter                       0.05                ;# Duty cycle distortion as a percentage of the whole
                                                            ;# cycle - +/- N%. Affects the falling edge
                                                            ;# of the clock [0.05]
set rm_ocv_derate_factor                0.05                ;# %. 0.10 = 10%, 0.05 = 5% [0.05]
set rm_critical_range                   0.1                 ;# Critical range. % of the rm_clock_period [0.1]
set rm_icg_name       		            $rm_clock_icg_cell  ;# Name of ICG cell
set rm_max_fanout                       16                  ;# Maximum fanout threshold [32]
set rm_cts_max_fanout                   16                  ;# Maximum fanout threshold [16]

# -----------------------------------------------------------------------------------
# Tool reporting defaults
# The numerical values in the [] brackets in the associated comment are some reasonable examples the user may adopt
# -----------------------------------------------------------------------------------
# Provide the number of significant digits when reporting the precision of timing and area reports 
set report_default_significant_digits 4   ;# [3.0]

# -----------------------------------------------------------------------------------
# Floorplan Control Setup
# The numerical values in the [] brackets in the associated comment are some reasonable examples the user may adopt
# -----------------------------------------------------------------------------------
set rm_core_utilization  0.5    ;# utilization ratio of the macro floorplan [0.5]
set rm_aspect_ratio      1		;# height-to-width ratio of the macro floorplan [1.00]
set rm_fp_exists         0      ;# Set this to 1 if you want synthesis to read in floorplanning scripts [0] 
set rm_fp_clustering     1      ;# Set this variable to 1 to enable local clustering of cells
                                ;# Usually clustering is the default option. But, for small designs, taking it out
                                ;# is better for routing [1]

# -----------------------------------------------------------------------------------
# Set Host Options
# -----------------------------------------------------------------------------------
# Logical names of libraries
set base_tt_1p80v_25c_lib      [list ${std_lib}${std_tt_1p80v_25c_opcond} ]

# IO Libs
set io_lib /lec/pdk/tsmc180/tpd018nv_280a/TSMCHOME/digital/Front_End/timing_power_noise/NLDM/tpd018nv_280a/tpd018nv

# -----------------------------------------------------------------------------------
# Nominal Library Set
# -----------------------------------------------------------------------------------
# TT(Typical-Typical) 1p80V 25C Libs
set tt_1p80v_25c_libs [list ${base_lib_dir}/${base_tt_1p80v_25c_lib}.lib]


# MACRO Libs
foreach macro ${macro_insts} {
  #if {[file exists "${rm_macro_dirs}/${macro}/${macro}_tt_1p80v_25c.lib"]} {
    # modified by Tianyao Xiao
    set tt_1p80v_25c_libs  [concat $tt_1p80v_25c_libs  ${rm_macro_dirs}/${macro}/${macro}_tt_1p8_25C_syn.lib] 
  #}
}


# -----------------------------------------------------------------------------------
# MMMC (Multi-Mode Multi-Corner) Definitions
# -----------------------------------------------------------------------------------
# Technology definition
set tech(process_node)              "180"
set tech(pdk_lib_path)              $rm_foundry_lib_dirs
set tech(lib_list_tt_1p80v_25c)     $tt_1p80v_25c_libs
set tech(lef_list)                  $rm_lef_reflib
#set tech(pdk_tech_path)             $rm_foundry_kit_dirs
set tech(driving_cell_lib)          $std_lib
set tech(driving_cell)              $rm_driving_cell
set tech(driving_pin)               $rm_driving_pin

# MMMC variables
set proj(corners) "tt_1p80v_25c"

# Set operating conditions values for each corner
# The variable format must be proj(<corner>,P/V/T)
# Use same values that are specified in liberty file
set proj(tt_1p80v_25c,P) "1"
set proj(tt_1p80v_25c,T) "25"
set proj(tt_1p80v_25c,V) "1.8"


# Set liberty libraries for each corner
# The varialbe format must be proj(library_set,<corner>)
set proj(library_set,tt_1p80v_25c)  $tech(lib_list_tt_1p80v_25c)


# Specify one sdc file for design's operation mode
set proj(constraints,func) ../../config/constraints_${rm_core_top}.sdc

# specify RTL directory
# 'pwd' == [syn|pnr]/logs
set SRC_DIR [exec pwd]/../../src

set macro_gds [list ]

# specify MACRO GDS directory
foreach macro ${macro_insts} {
    set macro_gds [concat ${macro_gds} [glob -nocomplain -directory ${rm_macro_dirs}/${macro} *.gds *.gds2]]
}
set gds_list [concat  ${base_gds_dir}/${std_lib}.gds \
                          ${macro_gds} \
                            /lec/pdk/tsmc180/tpd018nv_280a/TSMCHOME/digital/Back_End/gds/tpd018nv_280a/mt_2/6lm/tpd018nv.gds \
                            /lec/pdk/tsmc180/tpb018v_180a/TSMCHOME/digital/Back_End/gds/tpb018v_180a/cup/6lm/tpb018v.gds ]

# -----------------------------------------------------------------------------------
# Innovus Global TCL Variables
# -----------------------------------------------------------------------------------
set init_verilog            "../../syn/${rm_core_top}/${rm_core_top}_postsyn.v"
set init_lef_file           "${rm_lef_reflib}"
set init_mmmc_file          "../scripts/pnr_mmmc.tcl"
set init_top_cell           "${rm_core_top}"
set init_design_uniquify    {1}

set cell_height             3.92
set macro_halo_spc          [expr 1 * $cell_height]
set macro_halo_spc_4        [expr 4 * $cell_height]
set macro_halo_spc_2        [expr 2 * $cell_height]

# -----------------------------------------------------------------------------------
# v2lvs converting option
# -----------------------------------------------------------------------------------

set v2lvs_option "\
    -s ${rm_foundry_lib_dirs}/Back_End/spice/${std_lib}_270a/${std_lib}_270a.spi \
    -s /lec/pdk/tsmc180/tpd018nv_280a/TSMCHOME/digital/Back_End/spice/tpd018nv_270a/tpd018nv_1_2.spi \
    -s /lec/pdk/tsmc180/t018cmsp007k3_1_3a/tsmc18mm_1.8v_3.3v_T-018-CM-SP-007-K3_IC61_bsim4.5_pdk_v1.3a_20170119/PDK/Calibre/lvs/source.added "


foreach macro ${macro_insts} {
    set cdl_dir [file normalize ${rm_macro_dirs}/${macro}]
    set v2lvs_option "${v2lvs_option} -s ${cdl_dir}/${macro}.cdl"
}

