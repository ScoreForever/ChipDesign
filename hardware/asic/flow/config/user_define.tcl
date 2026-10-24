#####################################################################################
# Description:  User-Specific Definition file
# Author:       Mingxuan Li <mingxuanli_siris@163.com> [Peking University]
#####################################################################################

# Top module name
# ONLY ONE SPACE between 'rm_core_top' keyword and module name!
set rm_core_top my_soc_top
set rm_clock_period 20

# Set CHIPDESIGN_PROJECT_ROOT when the server checkout is not this repository.
# Keeping the default relative to this file avoids publishing a user-specific
# server path and makes the checked-in flow portable.
if {[info exists ::env(CHIPDESIGN_PROJECT_ROOT)]} {
    set PROJECT_ROOT [file normalize $::env(CHIPDESIGN_PROJECT_ROOT)]
} else {
    set PROJECT_ROOT [file normalize [file join [file dirname [info script]] .. .. .. ..]]
}
# List the names of the MACROs instantiated in design
# Directory format: src/macro/<name>/
# Dont comment this variable
# If no extra macro instances, leave a blank list
# ONLY ONE SPACE bwtween 'list' keyword and macro name!
#set macro_insts [list TS1DA32KX32 TS1DA32KX32_ANT]
set macro_insts [list RA1SHD_2048x32M8]

# ONLY ONE SPACE bwtween 'std_lib' keyword and std_lib name!
set std_lib tcb018gbwp7t
set cell_ext [list BWP7T]

# Specify active analysis view for timing analysis
# The variable values format must be func_<corner>
# possible func_<corner> value:
# func_tt_1p80v_25c
set proj(analysis_view,setup) "func_tt_1p80v_25c" 
set proj(analysis_view,hold)  "func_tt_1p80v_25c"

# Define synthesis optimization level
# Possible value: none | standard | extreme
# Choose extreme when timing constraint is tight
# Standard is enough for most cases
# None is the fastest theoretically
set syn_opt_level standard

# Define physical implementation effort
# Possible value: express | standard
# Choose express when fast prototyping is needed
# ONLY ONE SPACE bwtween 'pnr_opt_level' keyword and chosen level!
set pnr_opt_level standard

# Multi-CPU setting
# Recommended value: 32
# Maximum 112 for synthesis and 64 for physical implementation
# Greater CPUs may not lead to faster runtime
# For CLab server: 16.
set syn_cpus 16
set pnr_cpus 16

# Power net name
set init_gnd_net {VSS}
set init_pwr_net {VDD}

# Routing layer
# Possible value: METAL1 | METAL2 | METAL3 | METAL4 | METAL5 | METAL6
# M1 is NOT RECOMMENDED for routing
set topRoutingLayer     METAL6
set bottomRoutingLayer  METAL2


