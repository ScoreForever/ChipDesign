# ChipDesign

The group's repo for "Full-Stack Design and Practice of Advanced Chips" course
of Peking University.

## What's inside

- `hardware/npu/` — 完整 KWS-TinyCNN-8 INT8 NPU、基础计算单元和
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

Run the integrated SoC (ModelSim):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1
```

系统模型、量化、模块分工、MMIO接口和软硬件边界见
`docs/ARCHITECTURE.md`。
