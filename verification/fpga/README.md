# FPGA 验证与签核

当前设计为 **ABI 5.0（0x00050000）、8×8 PE、128 条描述符、两颗 8 KiB SRAM**。
[ABI 5.0 扩展报告](../../docs/SOC_EXPANSION_20261106.md)记录了 RTL 仿真和软件参考结果。
当前仓库归档的实板结果对应早期镜像，ABI 5.0 的 Vivado 实现和实板证据需随本轮候选镜像提交。

## 从哪里开始

1. 复制 [人工签核表](SIGNOFF_CHECKLIST.md) 到本轮证据目录，填写源码、构建、镜像、板卡和两路时钟的身份。
2. 按 [上板操作手册](NOCPU_BOARD_CHECK.md) 完成构建、下载、冒烟与自动板测。自动脚本产生的报告按原样保留。
3. 由 RTL 和 EDA 复核人分别检查动态 CDC 和布线后的 CDC 约束。由 FPGA 操作人执行停 TCK、复位、断电恢复和实际接线检查。
4. 用签核表逐项关联原始证据。复核人签字后再填写总判定。

| 目录 | 内容 |
|---|---|
| [board/](board/) | 冒烟、完整功能套件、实板停 TCK 与协议复位、物理复位哨兵、重配后恢复 |
| [cdc/](cdc/) | 实际 TAP 与突发握手 testbench、已布线 CDC 报告复核脚本、门禁单元检查 |
| [evidence/](evidence/) | 读取板测文件重算哈希及逐字节比对；另含历史完整数据集的离线审计 |
| [fixtures/](fixtures/) | CDC 门禁的固定反例输入 |
| [history/](history/) | 旧版复现工具及其适用范围 |
| [results/](results/) | 按日期保存的原始证据与历史报告 |

### 历史证据索引

| 范围 | 归档 |
|---|---|
| CPU/GDB、外部 I/O 和早期 CDC | [历史工作流](CPU_GDB_HISTORY.md)、[初次结果](results/2026-10-20/report.md)、[CDC 追加](results/2026-10-20-cdc/report.md)、[I/O 追加](results/2026-10-20-jtag-io/report.md) |
| ABI 3.0 FPGA 实现及实板自动回归 | [11 月 1 日基线](results/2026-11-01-no-cpu-int4/report.md) |
| 11 月 1 日镜像的追加验证 | [CDC 仿真与结构复核](results/2026-11-02-cdc-review/report.md)、[停 TCK 与复位](results/2026-11-02-cdc-board/report.md)、[忙时按键与断电重配](results/2026-11-02-physical-followup/report.md) |
| ABI 4.0 FPGA | [实现与实板自动回归](results/2026-11-04-int4-v2/report.md)、[七模型 112 帧实板结果](results/2026-11-04-int4-v2/zoo_supplement/report.md) |
| ABI 4.0 完整测试集软件评分 | [67,245 次软件整数评分](results/2026-11-04-int4-v2/full_dataset_accuracy/report.md) |
