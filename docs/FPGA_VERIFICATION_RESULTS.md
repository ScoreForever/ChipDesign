# FPGA 分层验证结果与边界

> 下文为CDC修复前的历史基线。当前修复版结果见[修复与验收记录](FPGA_CDC_CLOSURE_20261020.md)和[最新机器汇总](../verification/fpga/results/2026-10-20-cdc/report.json)，剩余测试问题见[审查记录](FPGA_TEST_REVIEW_20261020.md)。旧CDC告警不再代表当前网表。

2026-10-20，验证对象为 `main@9d25b09` 加本地 TCK H16/J5第21脚约束，50MHz。
本次未改 CPU/NPU/JTAG RTL。比特流 SHA256：`015e93fa87080f09380b687ffbdc8c5923483fc7e443555280c049a12e02a316`。

**功能回归通过；完整实现签核尚未通过。**
正式证据在 `build/fpga_validation_20261020_v2/`，入口为[工作流说明](../verification/fpga/README.md)、[PowerShell分阶段执行器](../verification/fpga/run.ps1)。
[机器汇总](../verification/fpga/results/2026-10-20/report.json)和[可读汇总](../verification/fpga/results/2026-10-20/report.md)由证据校验器生成，不靠人工修改PASS字段。
仓库保存此次汇总快照；完整日志、固件、原始读回及源码快照保留在本地上述build目录，不随Git发布。汇总记录的是当时验证的main提交，后续发布提交不改变该验证对象。

| 验证项 | 本次结果 |
|---|---|
| 真实烧写 | 指定板载下载线1234-tulA、xc7a35t，DONE=1，CRC_ERROR=0；烧写记录绑定manifest |
| CPU JTAG/GDB | 真实连接、BootROM、SRAM、寄存器与addi单步通过 |
| 板上推理 | 43组、190次，所有8个输出与独立整数golden逐位一致 |
| 固件检查 | 26,365项，包括非法参数、忙时拒绝、错误恢复、DONE/性能状态 |
| CPU IRQ路径 | 684次NPU/DMA pending/clear检查，经实际CPU mip读取 |
| 整片SRAM | 全8KiB的6种模式写入、verify、完整读回比较通过，并恢复原内容 |
| 复位恢复 | 6轮ndmreset，3轮打断持续任务；复位后额外12次推理全部通过 |
| NPU RTL | 7单元运行、21组golden、33,936逐元素比较；Pool1/Pool2/logits及事件轨迹；10类故障注入检查器验证 |
| SoC RTL | FPGA内存配置的7次ModelSim运行通过：SoC、DMA、容量、量化、SRAM延迟1/3等 |
| 已约束路径时序 | WNS +1.053ns，WHS +0.009ns；DRC没有Error/Critical Warning |
| CDC与约束签核 | 阻塞：复位跨域、同步链属性、bundled-data路径约束、外部IO时序假设 |

43组覆盖类数1..8、CPU字节/packed/DMA路径、随机/脉冲/极值输入、移位-31/0/31和整数边界、训练权重、100次模型复用。
CPU固件不存预期logits；主机使用独立参考计算进行比对。最终logits在板上检查，中间层数据在RTL仿真中检查。

当前签核阻塞包括：

1. `CDC-7 Critical 23`，系统域复位直接送入JTAG域，尚未证明其释放安全。
2. `CDC-2 Warning 2`，应答同步链缺少`ASYNC_REG`属性。
3. `cdc_2phase`注释要求对握手/数据跨域路径施加最大延迟；现有异步clock-group将这些路径切断，需要有依据的约束闭环。
4. `TIMING-18`，外部IO延迟假设未完整表达。已约束路径WNS为正不能覆盖这些遗漏。

这些问题保留在报告中，工作流因此返回非0；本次未将它们豁免或修改成PASS。修复后必须重新实现、烧写并运行新目录下的全部工作流。

尚未证明的范围：完整ISA符合性、形式验证/等价证明、功能与代码覆盖率收敛、布局后时序仿真、物理复位/PLL丢锁、电压温度与线缆电气裕量。
IRQ检查验证pending/clear，不包含中断入口、ISR和mret。训练权重使用合成特征，没有真实语音数据准确率结论。有限回归不等于所有RTL状态空间的穷尽验证。

本次还执行了先于正式重新烧写的基线回归：`build/fpga_functional_20261020/`和正式目录下`preprogram_board/`。
正式统计只计重新烧写之后的`board/`与`recovery/`，不累加重复执行结果。
