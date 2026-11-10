# 计划依据

- 当前生产架构是No-CPU、双8KiB SRAM、4×8 INT8 MAC，低精度为拟探索方案。
- ASIC结果记录：docs/NOCPU_DUAL_SRAM_PNR_20261026.md。综合标准单元1.313mm²、两宏0.803mm²；单TT角。PT与Formality的SRAM黑盒、DRC/LVS分类放行不能省略。
- 性能计数器定义参考docs/NPU_PERFORMANCE_COUNTERS.md，但部分文档对应旧版本，统计须以当前RTL和新回归为准。
- No-CPU上板入口参考verification/fpga/NOCPU_BOARD_CHECK.md；当前检查器是smoke，不自动证明板上镜像身份。旧verification/fpga/README.md中的CPU/GDB/DMI流程不能作为No-CPU验收替代。
- 工作区有用户未提交改动，基线不能只用HEAD命名，需冻结包含实际修改的输入快照。
