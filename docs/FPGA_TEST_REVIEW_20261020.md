# FPGA 测试审查（2026-10-20）

审查当前源码、实际路由报告、CPU验证固件及回归入口。旧版本的CDC-7关键23条和CDC-2两条告警已关闭。后续外部IO修复与标准测试入口集成已处理原前两项，具体签核模型见[JTAG外部时序](JTAG_IO_TIMING.md)；P2覆盖缺口仍保留。

| 优先级 | 问题与影响 | 依据及后续验收 |
|---|---|---|
| 已处理，保留物理假设 | 外部IO约束现已完整表达，TIMING-18归零；实际电气裕量仍需测量。 | FT232H短线接口模型、六组有效路径、setup/hold均检查；TMS及传播预算明确为未实测假设，不能据此声称任意线缆/PVT已认证。 |
| 已处理 | 专项测试现已进入标准Run/Rtl入口。 | 自动执行`test_report.py`的11项检查与`tb_cdc_reset.sv`仿真，日志哈希绑定manifest，失败阻止汇总。 |
| P2 | 活动任务复位不等于NPU忙时复位。当前先halt CPU、再经JTAG读取状态，最后才ndmreset；NPU在CPU暂停时继续跑，可能早已完成。 | `workflow.py` recovery中只确认magic运行中、jobs>0，没有在复位事件锁存BUSY或证明阶段。补充硬件/RTL触发机制，覆盖Conv1/Conv2/FC、DMA尾响应和DMI未决事务等复位位置。 |
| P2 | IRQ仅验证pending/clear，不能证明中断服务完整。 | `firmware/validate.c`通过mip读取，启动代码关闭全局中断。补充mtvec、mie、真实陷入、ISR清除和mret恢复；特别覆盖DMA/NPU同时中断。 |
| P2 | 板上profiler断言偏弱。issues==retires可在二者均0时通过，total>0也不能识别错误但非零的性能计数。 | `validate.c`检查profile[8]==profile[9]和perf>0。应与此固定配置的预期周期/issue/weight_rows等逐项比较，或明确哪些计数只能作为诊断信息。 |
| P2 | 随机和系统级覆盖没有收敛证据。100次soak复用同一模型/输入；训练模型输入是合成特征。 | 增加变化输入、模型交替、多seed/时钟相位、AXI并发与背压覆盖及覆盖率目标；真实语音准确率单列。芯片ISA符合性、物理复位/PLL丢锁、温压/电气裕量仍未验证。 |

专项复位台架只实例化一个32位mailbox，固定时钟比及有限数据、复位时机。它覆盖一次未完成事务与停TCK恢复，但不代表完整DMI请求/应答、所有相位和复位交织的协议证明。

本次审查没有将以上缺口改写成通过。已通过的43组最终logits比对及SRAM等测试仍有效，但“数万项CHECK通过”不能换算成功能或代码覆盖率百分比。

旧`build/fpga_validation_20261020_v2`及仓库`results/2026-10-20`是schema=1历史证据；新版报告器要求schema=2，不能对旧目录直接复算。新修复版的验收结果需单独保存并标明上述剩余限制。
