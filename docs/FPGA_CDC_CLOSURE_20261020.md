# FPGA CDC 与证据门禁修复（2026-10-20）

新实现：`build/fpga_cdc_closure_20261020_r2/`。CPU/NPU 算法未改；JTAG 外部复位增加独立 TCK 域同步释放，保持 ndmreset 不复位调试传输。同步链标记 ASYNC_REG；TCK LED 同样使用同步释放复位。

删除异步时钟组整体切断，改为两组 DMI mailbox 的 data/req/ack 六组端点约束。最大传播延迟取两域周期最小值（本配置20ns）；只对复位同步器的异步断言 CLR 引脚设 false path。实现与独立审计均查询实际 timing paths、有效 REQUIREMENT、路径数和 slack，路径被切断、遗漏或违例会报错。

实际 Vivado 2025.2 全新综合及布局布线成功，新 bit/DCP 已生成。WNS +0.758ns，WHS +0.013ns。CDC-7 从23项降至0，CDC-2 从2项降至0；CDC-3 Info 4，CDC-9 Info 1，CDC-15 Warning 75。六组 DMI 端点分别为41/1/1/34/1/1，最小 slack +18.511ns。CDC-15 是握手数据总线结构告警，保留可见；有效时序检查不等价于握手协议的形式证明。

验证通过：FPGA 配置7项 ModelSim 结构回归；专项测试覆盖停TCK、异步复位置位、四拍同步释放、未完成事务复位、恢复后新传输；5项证据测试覆盖伪造时序数值、注释冒充约束、路径被切断、非法复位状态和实际路由约束文件。

新门禁要求 schema=2：build_identity.json 绑定同一次构建的硬件输入、bit和DCP；烧写与审计核对该身份。timing.json、RTL统计、跨域路径、时钟交互、例外报告均记录哈希；时序数值另与原始报告交叉核对。复位恢复保存32字原始结果并重查 ABI、错误码、logits、IRQ及控制复位状态。

旧 schema=1 证据及已发布汇总保持历史用途，不迁移或补填新凭证。新版报告器会明确拒绝旧证据，必须生成新验证目录。

新验证目录为 `build/fpga_validation_cdc_20261020/`。新bit SHA256 为 `daf0bdd6f7e264864b3203330848f612f1e13afe50e39d5b875673977f237929`，经下载线1234-tulA烧写，DONE=1、CRC_ERROR=0。GDB检查、新bit的43组/190次板上推理、6种整片SRAM模式、6轮复位恢复（3轮打断持续任务，复位后12次推理）全部通过；NPU回归7单元/21组/33,936次逐元素比较及7项SoC结构回归通过。USB/IP枚举不一致通过重新附加同一FT232H解决，OpenOCD验证结束后CPU保持halt。

尚未关闭 TIMING-18 的外部 IO 时序假设，完整签核应继续 BLOCKED。没有用任意 IO delay 数字或 waiver 消除该问题。

最终schema=2验收目录：`build/fpga_validation_cdc_20261020_r3/`。恢复WSL USB附加与OpenOCD后，重新完成43组板上测试、6种SRAM模式、6轮复位及独立实现审计。最终报告证据校验通过，functional=PASS、physical=BLOCKED，唯一自动签核阻塞为TIMING-18；WNS +0.758ns、WHS +0.013ns。[最新报告快照](../verification/fpga/results/2026-10-20-cdc/report.md)与[机器汇总](../verification/fpga/results/2026-10-20-cdc/report.json)随源码发布。
新版证据单元检查5项通过，已存在的停TCK/未决事务专项仿真日志为Errors=0。测试入口集成及其他覆盖缺口见[测试审查](FPGA_TEST_REVIEW_20261020.md)，这些缺口不会因CDC关键告警清零而自动关闭。
