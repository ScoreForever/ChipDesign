# ChipDesign：可编程无 CPU NPU

`main` 是统一开发基线：**INT4 / ABI 5.0（0x00050000）：8×8 PE、128 条描述符、共享双 SRAM**。ABI 4 冻结时的版本来源、分支处置和验证边界见 [baseline 记录](docs/BASELINE_MAIN_20261106.md)；升级到 ABI 5 的改动见 [SoC 扩展记录](docs/SOC_EXPANSION_20261106.md)。

当前默认系统是 **主机 JTAG → 事务控制器 → 描述符调度 → 共享 INT4 NPU 后端**。
权重为 signed INT4，激活支持 i4/u4，每 byte 打包两个值；原始累加结果为 INT32。
每通道参数用一个 32-bit 字打包 18-bit bias、Q8 multiplier 和 6-bit shift；JSON 接口仍接收 Q15 multiplier，编译后采用单次舍入。
旧 ABI 3/4 模型包不兼容，必须重新编译。
支持 Conv（含 depthwise、矩形核和 1D）、FC、MaxPool、AvgPool/GAP、Add、ReLU、Requant；形状、参数、执行顺序由模型包提供。
常见 Conv/FC 后处理链通过局部缓冲直接传递，避免完整中间张量写回。
两颗 8 KiB SRAM 提供统一存储：模型镜像放在第二颗高地址端，激活可用空间为 `16384-model_bytes` B。
编译器按张量生命周期分配连续地址，大中间张量可以跨越两颗 SRAM；统一缓存、写合并和轮转读仲裁维护一致性。
本次实现与验证、复现命令及 EDA 交接见 [SoC 扩展记录](docs/SOC_EXPANSION_20261106.md)。
波形和逐算子 trace 为可选调试配置：ASIC 与生产 FPGA 构建默认关闭，普通仿真默认开启。
24 项全局性能计数器保留；主机根据能力寄存器判断调试功能是否可用。

- [架构与实现边界](docs/ARCHITECTURE.md)
- [模型格式、命令协议及主机使用](docs/CPU_LESS_CONTROL.md)
- [INT4 v2 最终设计报告](docs/INT4_V2_REPORT.md)
- [分层验证与复现](docs/VALIDATION_FLOW.md)
- [FPGA 验证与人工签核](verification/fpga/README.md)
- [完整测试集软件整数精度](verification/fpga/results/2026-11-04-int4-v2/full_dataset_accuracy/report.md)
- [INT4 重构与验证](docs/NPU_INT4_REFACTOR_20261031.md)
- [后续面积优化分析](docs/NPU_INT4_AREA_OPTIONS_20261031.md)
- [双SRAM实施与验证记录](docs/NPU_DUAL_SRAM_IMPLEMENTATION_20261026.md)
- [2026-10-23冻结基线验证结果](docs/NPU_PROGRAMMABLE_VALIDATION_20261023.md)

## 生成可运行模型包

从仓库根目录运行（Python 3.10+，基础工具仅用标准库）：

```powershell
python software/host/make_demo.py --output build/npu_demo
python software/host/npu_program.py build/npu_demo/model.json --output build/npu_demo/recompiled
```

也可对已有 INT8 训练 NPZ 进行显式有损 PTQ 导入（需要 NumPy）；此操作不保证分类精度，需另行校准、评估或 QAT：

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

回归覆盖 8×8、七种算子、融合/独立、INT4/u4 MAC、单次舍入、PTQ 包执行、控制事务、JTAG 引脚、
随机存储响应延迟、存储背压、复位与非法模型。`--quick` 只运行 8×8 原生控制接口。
真实课程宏仿真可以追加 `--macro-model build/private_ip/RA1SHD_2048x32M8.v`；工艺 IP 不提交。
该模式在统一存储内部实例化两颗同类型宏；未提供宏时使用同步行为存储。

## 主机推理

先配置 USB-JTAG 适配器并以 `software/host/nocpu_openocd.cfg` 启动 OpenOCD，再运行：

```powershell
python software/host/nocpu_jtag.py status
python software/host/nocpu_jtag.py infer --package build/npu_demo/package --input build/npu_demo/input.bin --output build/npu_demo/result.bin
```

ABI 4 历史 FPGA 证据覆盖 50 MHz 下七个模型的 112 帧 / 224 次实板运行；完整测试集的 67,245 次评分来自软件整数参考。
该 ABI 4 镜像 FPGA setup WNS 为 0.000 ns；ABI 4 历史 ASIC 签核为 TT corner，未覆盖多角 STA、IR/EM。
ABI 5 的 FPGA 实现与上板结果见 [2026-11-09 时序恢复验收](output/npu-timing/20261109/README.md)（50 MHz、routed WNS +0.533 ns，七模型首帧冷/热 14 次正确）；
ABI 5 尚无 ASIC 综合/PnR/签核数据（UNKNOWN），物理按键、电气波形检查仍未完成。
2026-11-10 合入模型双宏旁路、权重读取流水和 MAC 波前使能，功能验证见 [分支整合记录](docs/validation/branch_integration_20261110/README.md)；+0.533 ns 与板测仍只适用于冻结提交 `81876b3`，新 RTL 尚需重新实现和上板验收。
2026-10-23/24 等历史证据仅适用于当时冻结源码，不能替代当前版本验证。
新协议不兼容旧 TinyCNN MMIO/JTAG ABI；旧固定模型实现与旧测试入口已移除，历史可从 Git 获取。
训练和预处理由主机承担。早期 CPU/AXI 实现可从 [Git 历史快照](https://github.com/ScoreForever/ChipDesign/tree/8d475b26306ec9b6103fbcf622a5dde6504d6db2) 获取。
