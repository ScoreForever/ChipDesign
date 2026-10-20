# Top module name
set rm_core_top my_soc_top
set rm_clock_period 20

set PROJECT_ROOT /home/s2400012724/chip-fullstack-2026/ChipDesign_postsim_exps/pnr_no_cpu_areaopt_20261020
# The optimized no-CPU NPU stores weights in one course SRAM hard macro.
set macro_insts [list RA1SHD_2048x32M8]

set std_lib tcb018gbwp7t
set cell_ext [list BWP7T]

set proj(analysis_view,setup) "func_tt_1p80v_25c"
set proj(analysis_view,hold)  "func_tt_1p80v_25c"

set syn_opt_level standard

set pnr_opt_level standard

set syn_cpus 16
set pnr_cpus 16

set init_gnd_net {VSS}
set init_pwr_net {VDD}

set topRoutingLayer     METAL6
set bottomRoutingLayer  METAL2
