###############################################################################
# Purpose: Import the post-synthesis netlist and initialize Innovus.
# Course: Full-Stack Design and Practice of Advanced Chips
# Modifier: Zhuolin Li
# Acknowledgements: Mingxuan Li, for the original flow and configuration.
#
# Basis: Adapted from the original initialization flow and this project's
#        recorded Innovus operations.
# Scope: Design import and tool setup only; no floorplanning or placement.
###############################################################################

# 根据脚本位置确定项目路径，避免依赖启动时所在目录。
set pnr_scripts_dir [file dirname [file normalize [info script]]]
set pnr_project_dir [file dirname [file dirname $pnr_scripts_dir]]

# 防止在已经加载设计的会话中重复初始化。
if {[dbGet top.name] ni {0x0 {}}} {
    error "Design already loaded. Run this script in a new Innovus session."
}

# 原 global_define 中的相对路径，以 pnr/logs 为基准。
set pnr_work_dir [file join $pnr_project_dir pnr logs]
file mkdir $pnr_work_dir
cd $pnr_work_dir

# user_define：顶层、时钟、工艺库名称、CPU 数、优化等级。
# global_define：LEF、Liberty、SDC、captable、综合网表的位置。
source [file join $pnr_project_dir config user_define.tcl]
source [file join $pnr_project_dir config global_define.tcl]

if {$pnr_opt_level ni {standard express}} {
    error "Unsupported pnr_opt_level: $pnr_opt_level"
}
set pnr_init_settings \
    [file join $pnr_scripts_dir init_pnr_${pnr_opt_level}.tcl]

# 提前检查输入文件是否可读。
# 这里只检查路径，不代表库内容或时序已经验收通过。
set pnr_input_files [concat \
    [list $init_verilog $init_mmmc_file $rm_captable_file \
          $proj(constraints,func) $pnr_init_settings] \
    $init_lef_file]

foreach corner $proj(corners) {
    set pnr_input_files \
        [concat $pnr_input_files $proj(library_set,$corner)]
}

foreach input_file $pnr_input_files {
    if {![file readable $input_file]} {
        error "Missing or unreadable PNR input: $input_file"
    }
}

# 明确工具单位。
# 当前 Innovus 版本会忽略 SDC 中的 set_units。
setLibraryUnit -cap 1pf -time 1ns
set_global timing_derate_spatial_distance_unit 1nm

# 读取 init_verilog、init_lef_file 和 init_mmmc_file。
# pnr_mmmc.tcl 会创建分析视图并读取 SDC，无需重复 read_sdc。
init_design

if {[dbGet top.name] ne $rm_core_top} {
    error "Imported top does not match rm_core_top=$rm_core_top"
}

# 保持原来的执行顺序：先导入，再应用优化和布线参数。
source $pnr_init_settings

# init_design 可能生成默认标准单元行，不能用 numRows 判断是否执行过本阶段。
# 仅本入口成功导入的设计允许开始一次 Floorplan。
set pnr_floorplan_pending 1

puts "Import completed: [dbGet top.name]. Floorplan has not started."
