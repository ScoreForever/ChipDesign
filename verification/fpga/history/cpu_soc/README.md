# 历史CPU SoC验证归档

这里保存2026年10月CPU/AXI/DMA/GDB架构的验证说明，不属于当前ABI 5.0无CPU NPU的操作流程或签核要求。

当前入口：[FPGA验证](../../README.md)、[无CPU上板流程](../../NOCPU_BOARD_CHECK.md)、[人工签核表](../../SIGNOFF_CHECKLIST.md)。

| 文档 | 历史内容 |
|---|---|
| [CPU/GDB工作流](CPU_GDB_HISTORY.md) | CPU单步、固件、DMA与CPU IRQ检查 |
| [本地环境](LAB6_LOCAL_ENV.md) | 旧CPU SoC的RISC-V工具链及设备配置 |
| [板上验证](LAB6_MAIN_BOARD_VALIDATION.md) | 2026-10-20的配置下载与CPU连通性 |
| [分层验证结果](FPGA_VERIFICATION_RESULTS.md) | 旧系统的功能与签核边界 |
| [CDC修复](FPGA_CDC_CLOSURE_20261020.md) | DMI邮箱同步链和历史约束 |
| [测试审查](FPGA_TEST_REVIEW_20261020.md) | CPU/IRQ、DMA和复位的历史缺项 |
| [DRC修正](FPGA_DRC_CLOSURE.md) | 旧CPU/AXI/GAP系统的BRAM与DRC结果 |
| [结构优化](FPGA_STRUCTURE_OPTIMIZATION.md) | 旧系统的容量、资源和集成测试 |

原始证据仍在 `verification/fpga/results/2026-10-20*`，按原有字节保留。文档的旧入口路径以历史Git快照为准；迁移不表示旧脚本恢复为现行支持接口。
