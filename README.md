# ChipDesign

The group's repo for "Full-Stack Design and Practice of Advanced Chips" course
of Peking University.

## What's inside

- `hardware/npu/` — 完整四分类 KWS-TinyCNN-8-Flat INT8 NPU、基础计算单元和
  Icarus兼容的自检回归。
- `hardware/soc/` — Integrated SoC with `cv32e40p` CPU, AXI crossbar, bootram,
  SRAM, JTAG debug, and the NPU behind an MMIO wrapper (ModelSim).

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

Run the integrated SoC (ModelSim):

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
