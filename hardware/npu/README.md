# 可编程 NPU

当前设计已升级 ABI 5：8×8 PE、128 描述符、双 SRAM 共享激活空间；旧 ABI 4 二进制必须重编译。当前实现与验证以仓库 `docs/SOC_EXPANSION_20261106.md` 为准；ABI 5 的 FPGA 实现与上板结果见 `output/npu-timing/20261109/README.md`。下面旧版测量只对应历史源码。
当前 INT4 设计使用一套描述符驱动执行核，支持 Conv（含 depthwise）、FC、MaxPool、AvgPool/GAP、Add、ReLU、Requant。

- [架构、数值和融合边界](../../docs/ARCHITECTURE.md)
- [模型格式与主机协议](../../docs/CPU_LESS_CONTROL.md)
- [统一 main baseline](../../docs/BASELINE_MAIN_20261106.md)
- [INT4 v2 最终设计报告](../../docs/INT4_V2_REPORT.md)
- [双SRAM实施与验证](../../docs/NPU_DUAL_SRAM_IMPLEMENTATION_20261026.md)
- [改造前2026-10-23验证证据](../../docs/NPU_PROGRAMMABLE_VALIDATION_20261023.md)

运行 `python hardware/npu/scripts/run_program_regression.py --output build/program_regression`。
非对齐激活 gather 专项：`python hardware/npu/scripts/run_gather_regression.py --output build/gather_regression`。
该测试覆盖 2/3/4/5/7/9 通道、有符号/无符号、padding、步长和尾通道，并检查 4 通道 3×3 用例的周期与读请求预算。
默认 RTL 在 `rtl/`，旧固定网络与重复算子实现已删除；`tools/tinycnn8_golden.py` 仅作为
独立旧模型数值参考，不能代表生产硬件或主机协议。
完整程序回归还包含环形写队列转发优先级检查，覆盖 18,432 组队列头、占用与地址匹配组合。
生产实例为两颗 8 KiB 单端口 SRAM 组成的共享存储（`npu_shared_store`）：模型镜像在第二颗高端，激活可用 `16384-model_bytes` B；
激活经 64 B 读缓存、写合并/转发和小队列访问；模型读取使用旁路，可与另一颗宏上的激活访问并行，同颗宏访问互斥。`npu_model_store.sv` 中的独立模型存储只用于块级回归。
宏仿真定义 `NPU_USE_RA1SHD_MODEL` 与 `NPU_USE_RA1SHD_ACTIVATION`，并提供课程宏模型。

2026-11-10 分支整合加入深度 3 的权重读取流水队列及 MAC 波前寄存器使能；见 [整合验证记录](../../docs/validation/branch_integration_20261110/README.md)。此前 +0.533 ns 和实板结果对应 `81876b3`，不自动适用于这些新 RTL。
