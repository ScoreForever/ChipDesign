# ChipDesign SoC Integration

This directory contains the integrated System-on-Chip: a `cv32e40p` RISC-V core
connected through an AXI crossbar to bootram, SRAM, JTAG debug, and the
ChipDesign NPU (Weight-Stationary Matrix Unit + INT8 SIMD Vector Unit).

For the full system architecture, address map, data formats, and programming
model, see [`docs/ARCHITECTURE.md`](/docs/ARCHITECTURE.md).

## Directory Layout

```
hardware/soc/
├── rtl/
│   ├── axi/              # AXI crossbar, adapters, converters
│   ├── common_cells/     # FIFOs, arbiters, counters
│   ├── clk_rst/          # Clock gating and reset generation
│   ├── debug/            # RISC-V debug module (dm_top, dmi_jtag)
│   ├── mem/              # bootram and SRAM
│   ├── include/          # my_soc_pkg, AXI typedef/assign helpers
│   ├── npu/
│   │   └── npu_mmio_wrapper.sv   # MMIO bridge to Matrix/Vector Unit
│   ├── my_npu_subsystem.sv
│   └── my_soc_top.sv
├── sim/
│   ├── filelists/chipdesign_soc.f
│   ├── scripts/run_soc.ps1
│   ├── sw/chipdesign_npu_test.S
│   ├── sw/chipdesign_npu_test.hex
│   ├── sw/gen_hex.py
│   └── tb/chipdesign_soc_tb.sv
└── README.md
```

## Architecture

```
                      +-----------+
clk/rst/tck/tms/td -->| my_soc_top |
                      +-----+-----+
                            |
         +------------------+------------------+
         |                  |                  |
    +----v----+        +----v----+        +----v----+
    |  CPU    |        |  bootram |        |  SRAM   |
    |cv32e40p |        | (64 B)   |        | (8 KiB) |
    +----+----+        +----+-----+        +----+----+
         |                  |                  |
         +------------------+------------------+
                            |
                     +------v-------+
                     |   AXI xbar   |
                     +------+-------+
                            |
              +-------------+-------------+
              |                           |
        +-----v-----+             +-------v-------+
        |   debug   |             |  NPU wrapper  |
        | (JTAG/DM) |             | matrix+vector |
        +-----------+             +---------------+
```

## Address Map

| Region | Base       | Length     | Notes                         |
| ------ | ---------- | ---------- | ----------------------------- |
| Debug  | 0x0000_0000| 0x0000_1000| RISC-V debug module           |
| Boot   | 0x0001_0000| 0x0001_0000| Bootram (jump to SRAM)        |
| NPU    | 0x7000_0000| 0x0000_4000| Matrix + Vector Unit MMIO     |
| SRAM   | 0x8000_0000| 0x1000_0000| Main memory (8 KiB model)     |

## NPU MMIO Register Map

All offsets are relative to `NPU_BASE` (0x7000_0000).

| Offset | Name              | Access | Description                                      |
| ------ | ----------------- | ------ | ------------------------------------------------ |
| 0x0000 | MATRIX_CTRL       | W      | bit0=start compute, bit1=load weights            |
| 0x0004 | MATRIX_STATUS     | R      | bit0=idle, bit1=weights_loaded, bit2=out_valid   |
| 0x0008 | VECTOR_CTRL       | W      | {dst_sel, src_b_sel, src_a_sel, opcode}          |
| 0x000C | VECTOR_LANE_MASK  | W      | 8-bit lane mask                                  |
| 0x0010 | VECTOR_SCALAR     | W      | signed INT8 scalar                               |
| 0x0014 | VECTOR_STATUS     | R      | bit0=out_valid                                   |
| 0x0018 | VECTOR_OP         | W      | trigger one vector operation                     |
| 0x0020 | VECTOR_SRC_A_LO   | W      | low 32 bits of vec_a                             |
| 0x0024 | VECTOR_SRC_A_HI   | W      | high 32 bits of vec_a                            |
| 0x0028 | VECTOR_SRC_B_LO   | W      | low 32 bits of vec_b                             |
| 0x002C | VECTOR_SRC_B_HI   | W      | high 32 bits of vec_b                            |
| 0x0030 | VECTOR_OUT_LO     | R      | low 32 bits of vec_out                           |
| 0x0034 | VECTOR_OUT_HI     | R      | high 32 bits of vec_out                          |
| 0x0040 | MATRIX_WEIGHT[0]  | W      | weight staging (8 words for default 4x8)         |
| ...    | ...               | W      |                                                  |
| 0x005C | MATRIX_WEIGHT[7]  | W      |                                                  |
| 0x0100 | MATRIX_ACT        | W      | 4 INT8 activations packed                        |
| 0x0110 | MATRIX_PSUM[0]    | W      | 8 INT32 partial sums                             |
| ...    | ...               | W      |                                                  |
| 0x012C | MATRIX_PSUM[7]    | W      |                                                  |
| 0x0200 | MATRIX_OUT[0]     | R      | 8 INT32 outputs                                  |
| ...    | ...               | R      |                                                  |
| 0x021C | MATRIX_OUT[7]     | R      |                                                  |

## Running the Regression

From the repository root, with ModelSim on `PATH`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1
```

The script compiles the full SoC filelist, runs `chipdesign_soc_tb`, and reports
`PASS integrated SoC regression` on success.

To run on a remote EDA server via SSH:

```powershell
$env:SSH_HOST = "agi"
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1
```

## Software Test

`hardware/soc/sim/sw/chipdesign_npu_test.S` is a minimal RISC-V assembly program
loaded into SRAM. It:

1. Writes `RUNNING` (0x12345678) to the magic status region at 0x8000_1FE0.
2. Loads a 4x8 weight tile (all weights = 1).
3. Writes activations `[1,1,1,1]` and partial sums `[0,...]`.
4. Starts the matrix compute and polls `MATRIX_STATUS`.
5. Reads `MATRIX_OUT[0]` and checks it equals 4.
6. Writes `PASS` (0xC0DEC0DE) or `FAIL` (0xDEADBEEF) to magic status.

The corresponding `chipdesign_npu_test.hex` was generated by `gen_hex.py` and is
loaded into the SRAM model at simulation start.

## Notes

- The AXI fabric uses SystemVerilog interfaces and concurrent assertions, so the
  SoC must be compiled with ModelSim/VCS. It cannot be compiled with Icarus.
- The standalone NPU unit tests under `hardware/npu/` continue to run with
  Icarus and are unaffected by this integration.
- Synthesis and timing closure are out of scope for this functional integration.
