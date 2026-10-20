# FPGA 测试审查（2026-10-20）

审查当前 CDC 修复版源码、实际路由报告、CPU验证固件及回归入口。旧版本的CDC-7关键23条和CDC-2两条告警已在新实现中消失，不能继续当作当前未修复项。下列问题仍存在；前两项应优先处理。

| 优先级 | 问题与影响 | 依据及后续验收 |
|---|---|---|
| P1 | 外部IO时序签核未完成。功能和内部时序通过，仍不能证明TDI/TMS/TDO的板级采样裕量。 | 新route_methodology仍有TIMING-18五项。建立有器件/板级依据的IO时序假设，结合采样边沿审查，不能随填delay消警。 |
| P1 | 新增专项测试未进入标准Run/Rtl入口。以后回归可跳过这次CDC修复最相关的测试。 | `verification/fpga/run.ps1` Rtl阶段仅调用原NPU和SoC两套脚本，未执行`tb_cdc_reset.sv`、`test_report.py`。应自动执行并将日志/结果绑定manifest，失败阻止汇总。 |
| P2 | 活动任务复位不等于NPU忙时复位。当前先halt CPU、再经JTAG读取状态，最后才ndmreset；NPU在CPU暂停时继续跑，可能早已完成。 | `workflow.py` recovery中只确认magic运行中、jobs>0，没有在复位事件锁存BUSY或证明阶段。补充硬件/RTL触发机制，覆盖Conv1/Conv2/FC、DMA尾响应和DMI未决事务等复位位置。 |
| P2 | IRQ仅验证pending/clear，不能证明中断服务完整。 | `firmware/validate.c`通过mip读取，启动代码关闭全局中断。补充mtvec、mie、真实陷入、ISR清除和mret恢复；特别覆盖DMA/NPU同时中断。 |
| P2 | 板上profiler断言偏弱。issues==retires可在二者均0时通过，total>0也不能识别错误但非零的性能计数。 | `validate.c`检查profile[8]==profile[9]和perf>0。应与此固定配置的预期周期/issue/weight_rows等逐项比较，或明确哪些计数只能作为诊断信息。 |
| P2 | 随机和系统级覆盖没有收敛证据。100次soak复用同一模型/输入；训练模型输入是合成特征。 | 增加变化输入、模型交替、多seed/时钟相位、AXI并发与背压覆盖及覆盖率目标；真实语音准确率单列。芯片ISA符合性、物理复位/PLL丢锁、温压/电气裕量仍未验证。 |

专项复位台架只实例化一个32位mailbox，固定时钟比及有限数据、复位时机。它覆盖一次未完成事务与停TCK恢复，但不代表完整DMI请求/应答、所有相位和复位交织的协议证明。

本次审查没有将以上缺口改写成通过。已通过的43组最终logits比对及SRAM等测试仍有效，但“数万项CHECK通过”不能换算成功能或代码覆盖率百分比。

旧`build/fpga_validation_20261020_v2`及仓库`results/2026-10-20`是schema=1历史证据；新版报告器要求schema=2，不能对旧目录直接复算。新修复版的验收结果需单独保存并标明上述剩余限制。
