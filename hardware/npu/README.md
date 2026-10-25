# 可编程 NPU

当前设计只使用一套描述符驱动执行核，支持 Conv、FC、MaxPool、ReLU、Requant。

- [架构、数值和融合边界](../../docs/ARCHITECTURE.md)
- [模型格式与主机协议](../../docs/CPU_LESS_CONTROL.md)
- [双SRAM实施与验证](../../docs/NPU_DUAL_SRAM_IMPLEMENTATION_20261026.md)
- [改造前2026-10-23验证证据](../../docs/NPU_PROGRAMMABLE_VALIDATION_20261023.md)

运行 `python hardware/npu/scripts/run_program_regression.py --output build/program_regression`。
默认 RTL 在 `rtl/`，旧固定网络与重复算子实现已删除；`tools/tinycnn8_golden.py` 仅作为
独立旧模型数值参考，不能代表生产硬件或主机协议。
模型/激活默认各8 KiB；激活端为单端口SRAM加64 B读缓存、写合并/转发和小队列。
宏仿真定义 `NPU_USE_RA1SHD_MODEL` 与 `NPU_USE_RA1SHD_ACTIVATION`，并提供课程宏模型。
