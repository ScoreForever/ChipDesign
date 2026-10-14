# ChipDesign

The group's repo for "Full-Stack Design and Practice of Advanced Chips" course
of Peking University.

## What's inside

- `hardware/npu/` — 当前 GAP 分类头的 TinyCNN-8 INT8 NPU、基础计算单元和
  Icarus兼容的自检回归。
- `hardware/soc/` — Integrated SoC with `cv32e40p` CPU, AXI crossbar, bootram,
  SRAM, JTAG debug, and the NPU behind an MMIO wrapper (ModelSim).

## Quick start

Run NPU unit tests (Icarus; `run_all_tests.ps1` also includes legacy network tests
that still need adaptation to current GAP RTL):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_matrix_unit_test.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_vector_unit_test.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_all_tests.ps1
```

Historical synthetic TinyCNN schedule/golden/MMIO regression entry point:

```sh
python3 hardware/npu/scripts/run_tinycnn8_regression.py --output /path/to/new-empty-directory
```

The experiment compares original, gather/load overlap and spatial-tile schedules with the
same compute core. [Measured results and trade-offs](docs/kws_tinycnn8_tiled/REPORT.md),
[design decisions and contribution scope](docs/kws_tinycnn8_tiled/THOUGHTS.md), and
[reproduction details](hardware/npu/sim/README_tinycnn8.md) describe the fixed
behavioral-memory contract. Its recorded results predate the current GAP/Flat
integration mismatch and are not current-main acceptance evidence.

当前核为 `Conv1 → Pool1 → Conv2 → Pool2 → GAP → FC(8→1..8)`，软件导出器
仍生成 Flatten 模型；MMIO layer2 commit 又拒绝 GAP 参数。当前部署限制和
设计细节见 [系统架构说明](docs/ARCHITECTURE.md)。
当前结构回归入口为 `hardware/soc/sim/scripts/run_structure_regression.ps1`。

Run the integrated SoC (ModelSim):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1
```

系统模型、量化、模块分工、MMIO接口和软硬件边界见
`docs/ARCHITECTURE.md`。
