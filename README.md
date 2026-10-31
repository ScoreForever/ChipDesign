# ChipDesign：可编程无 CPU NPU

当前默认系统是 **主机 JTAG → 事务控制器 → 描述符调度 → 共享 NPU 后端**，协议 ABI 2.1（2.0 的基础上压缩通道数不足4的末尾权重 tile，旧模型包需重新编译）。
支持 Conv、FC、MaxPool、ReLU、Requant；形状、参数、执行顺序由模型包提供。
常见 Conv/FC 后处理链通过局部缓冲直接传递，避免完整中间张量写回。
默认模型与激活分别使用8 KiB存储；ASIC入口将两者映射为独立SRAM硬宏，
激活端保留64 B读缓存、字节写合并/转发和小写队列，避免完整张量寄存器阵列。
波形trace为可选调试配置：ASIC默认关闭16 KiB波形RAM，仿真/FPGA默认开启；
性能计数器和逐算子时间线摘要保留，主机根据能力寄存器判断波形是否可用。

- [架构与实现边界](docs/ARCHITECTURE.md)
- [模型格式、命令协议及主机使用](docs/CPU_LESS_CONTROL.md)
- [双SRAM实施与验证记录](docs/NPU_DUAL_SRAM_IMPLEMENTATION_20261026.md)
- [芯片工作原理交互动画（离线 HTML）](docs/animations/npu-system/standalone.html)：11 章教学、卷积与阵列逐步演示、当前 RTL 真实逐周期回放；[使用与复现说明](docs/animations/npu-system/README.md)。
- [2026-10-23冻结基线验证结果](docs/NPU_PROGRAMMABLE_VALIDATION_20261023.md)

## 生成可运行模型包

从仓库根目录运行（Python 3.10+，基础工具仅用标准库）：

```powershell
python software/host/make_demo.py --output build/npu_demo
python software/host/npu_program.py build/npu_demo/model.json --output build/npu_demo/recompiled
```

也可导入已有训练 NPZ（此路径需要 NumPy）：

```powershell
python software/host/npu_program.py software/models/tinycnn8_flatten_strong.npz --output build/trained_program
```

模型包包含 `model.bin` 和 `manifest.json`。所有网络结构转换发生在主机，硬件不解析 JSON。

## 完整回归

安装 Icarus Verilog，将 `iverilog`、`vvp` 加入 PATH，然后运行：

```powershell
python hardware/npu/scripts/run_program_regression.py --output build/program_regression
python hardware/npu/scripts/run_trace_configuration.py --output build/trace_configuration
python software/host/explore_conv_streaming.py --output build/conv_streaming.json
```

回归覆盖 4×8/4×4、五种算子、融合/独立、旧模型数值迁移、控制事务、JTAG 引脚、
随机存储响应延迟、存储背压、复位与非法模型。`--quick` 只运行 4×8 原生控制接口。
真实课程宏仿真可以追加 `--macro-model build/private_ip/RA1SHD_2048x32M8.v`；工艺 IP 不提交。
该模式为模型和激活各实例化一颗同类型宏；未提供宏时使用同步行为存储。

## 主机推理

先配置 USB-JTAG 适配器并以 `software/host/nocpu_openocd.cfg` 启动 OpenOCD，再运行：

```powershell
python software/host/nocpu_jtag.py status
python software/host/nocpu_jtag.py infer --package build/npu_demo/package --input build/npu_demo/input.bin --output build/npu_demo/result.bin
```

验证结论必须对应具体源码；2026-10-23/24的FPGA及板级记录属于改造前冻结基线，
双SRAM版本的功能、时序和物理验证边界见实施记录。
新协议不兼容旧 TinyCNN MMIO/JTAG ABI；旧固定模型实现与旧测试入口已移除，历史可从 Git 获取。
训练/预处理仍由主机承担。仓库保留的 CPU、AXI 和课程资料不属于默认实例树。
