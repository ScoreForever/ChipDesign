# ChipDesign

本分支默认 FPGA 系统已改为 **无 CPU：专用 JTAG 主机接口 → 硬件控制器 → 融合 NPU**。
模型/输入完整性、模型更新、忙时保护、结果快照、超时恢复和空闲存储读回均已实现。
接口、使用方法、验证和综合结果见 [无 CPU 控制系统](docs/CPU_LESS_CONTROL.md)。
旧 CPU 系统保留为 `my_soc_cpu_reference`，供历史测试和对照使用。

The group's repo for "Full-Stack Design and Practice of Advanced Chips" course
of Peking University.

## What's inside

- `hardware/npu/` — 完整四分类 KWS-TinyCNN-8-Flat INT8 NPU、基础计算单元和
  Icarus兼容的自检回归。
- `hardware/soc/` — CPU-less JTAG controller and native NPU integration;
  the former CV32E40P/AXI/MMIO system remains as a reference.

## Quick start

Run NPU unit tests (Icarus):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_matrix_unit_test.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_vector_unit_test.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_all_tests.ps1
```

Run the reproducible synthetic TinyCNN schedule/golden/MMIO regression:

```sh
python3 hardware/npu/scripts/run_tinycnn8_regression.py --output /path/to/new-empty-directory
```

This compares original, gather/load overlap and spatial-tile schedules with the
same compute core. [Measured results and trade-offs](docs/kws_tinycnn8_tiled/REPORT.md),
[design decisions and contribution scope](docs/kws_tinycnn8_tiled/THOUGHTS.md), and
[reproduction details](hardware/npu/sim/README_tinycnn8.md) describe the fixed
behavioral-memory contract. It does not establish trained KWS accuracy or PPA.

Run the CPU-less controller, JTAG pin and Python host API regression (Icarus):

```sh
python hardware/soc/sim/scripts/run_nocpu.py --output build/nocpu_regression
```

Run the legacy CPU reference SoC (ModelSim):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1
```

默认SoC使用融合Flat核心（`FUSED_CORE=1`）。运行融合核与两种MMIO核心选择回归：

```sh
python3 hardware/npu/scripts/run_tinycnn8_fused_test.py
```

系统模型、量化、模块分工、MMIO接口和软硬件边界见
`docs/ARCHITECTURE.md`。

当前融合核的[真实语音与逐周期验证](docs/FUSED_SPEECH_VALIDATION.md)，以及
保持RTL兼容的[模型准确率实验](docs/MODEL_ACCURACY_OPTIMIZATION.md)包含2026-10-14
的agi实测、复现入口和证据边界。

存储配置：默认（流片配置）主存一路使用课程提供的 `axi2mem` / `my_mainmem` / `sram_ff`，
实例路径保持 `i_mainmem/i_mainmem`，便于替换为课程 SRAM 宏。EGo1 构建定义
`CHIPDESIGN_FPGA_MEM`，改用 `hardware/fpga/rtl/` 下 3 拍读流水的 FPGA 专用模块。
结构回归、KWS workload、SAIF 与剖析脚本的 Linux 入口用 `--config asic|fpga` 选择
（默认 `asic`），remote PowerShell 入口用 `-Config asic|fpga`；FPGA 功耗分析请用 `fpga`。
