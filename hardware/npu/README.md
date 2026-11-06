# 可编程 NPU

当前设计已升级 ABI 5：8×8 PE、128 描述符、双 SRAM 共享激活空间；旧 ABI 4 二进制必须重编译。当前实现与验证以仓库 `docs/SOC_EXPANSION_20261106.md` 为准，下面旧版测量只对应历史源码。
当前 INT4 设计使用一套描述符驱动执行核，支持 Conv（含 depthwise）、FC、MaxPool、AvgPool/GAP、Add、ReLU、Requant。

- [架构、数值和融合边界](../../docs/ARCHITECTURE.md)
- [模型格式与主机协议](../../docs/CPU_LESS_CONTROL.md)
- [统一 main baseline](../../docs/BASELINE_MAIN_20261106.md)
- [INT4 v2 最终设计报告](../../docs/INT4_V2_REPORT.md)
- [双SRAM实施与验证](../../docs/NPU_DUAL_SRAM_IMPLEMENTATION_20261026.md)
- [改造前2026-10-23验证证据](../../docs/NPU_PROGRAMMABLE_VALIDATION_20261023.md)

运行 `python hardware/npu/scripts/run_program_regression.py --output build/program_regression`。
默认 RTL 在 `rtl/`，旧固定网络与重复算子实现已删除；`tools/tinycnn8_golden.py` 仅作为
独立旧模型数值参考，不能代表生产硬件或主机协议。
模型/激活默认各8 KiB；激活端为单端口SRAM加64 B读缓存、写合并/转发和小队列。
宏仿真定义 `NPU_USE_RA1SHD_MODEL` 与 `NPU_USE_RA1SHD_ACTIVATION`，并提供课程宏模型。
