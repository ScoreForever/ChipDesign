# ChipDesign

The group's repo for "Full-Stack Design and Practice of Advanced Chips" course
of Peking University.

## What's inside

- `hardware/npu/` — Weight-stationary Matrix Unit and INT8 SIMD Vector Unit
  (Icarus-compatible unit tests).
- `hardware/soc/` — Integrated SoC with `cv32e40p` CPU, AXI crossbar, bootram,
  SRAM, JTAG debug, and the NPU behind an MMIO wrapper (ModelSim).

## Quick start

Run NPU unit tests (Icarus):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_matrix_unit_test.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_vector_unit_test.ps1
```

Run the integrated SoC (ModelSim):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1
```

See `hardware/soc/README.md` for integration details and `docs/ARCHITECTURE.md`
for the full system architecture and programming model.
