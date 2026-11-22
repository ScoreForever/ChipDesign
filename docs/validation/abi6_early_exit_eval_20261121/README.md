# ABI 6.0 Three-in-One Extension: Early-Exit (`OP_EXIT=8`), BitNet-1.58b / INT2 Weight Unpacking, and Zero-Pin JTAG Fast-Stream

## 1. Overview
Under the strict TSMC 180 nm `1980×1980 μm` sealed die envelope (`78.4%` core utilization, `7` signal pins, `16 KB` single-port unified SRAM), ABI 6.0 adds three backward-compatible, ultra-low-area architectural extensions without altering the `8×8` MAC array or adding any top-level pins:

1. **Hardware Dynamic Early-Exit & Conditional Forward Branch (`OP_EXIT = 8`)**:
   - Reuses the existing 64-byte descriptor (`d[0][19:18]` mode: `margin`/`top1`/`always`, `d[7]` signed 32-bit threshold, `d[8]` `{exit_id, target_pc}`).
   - Enforces strictly forward DAG jumps (`pc < target_pc <= op_count`) verified statically at `MODEL_COMMIT` via `npu_tensor_lookup` (zero infinite-loop risk, zero provenance corruption).
   - Exposes read-only telemetry at `0x70` (`EXIT_STATUS = {exit_taken, 7'd0, exit_id, exit_pc, top1_idx}`) and `0x74` (`EXIT_MARGIN = max1 - max2`), and updates capability register `0x0c` to `0xff` while keeping version `0x00 = 0x00050000` for backward compatibility.
2. **Layer-Wise Mixed-Precision Weight Unpacking (`i4` / `t2` BitNet-1.58b / `i2` / `s2`)**:
   - Uses `d[0][21:20]` (`w_mode`) on `Conv`/`FC` to unpack 2-bit packed weights (`2 bytes/row` for 8 lanes, **2.0× weight compression**) on-the-fly into the existing 4-bit MAC input bus.
   - Supports `t2` (BitNet-1.58b ternary `{-1, 0, +1}`), `i2` (signed INT2 `{-2, -1, 0, +1}`), and `s2` (symmetric INT2 `{-2, -1, +1, +2}`) across Conv, FC, Depthwise Conv, fused Requant/ReLU/MaxPool, and skewed K-groups.
3. **Zero-Pin JTAG Fast-Stream (`IR=15h STREAM_WRITE`, `IR=16h STREAM_READ`)**:
   - Adds a 5-bit `stream_cnt` inside `npu_jtag_transport` (`tck_i` domain only; zero `clk_i`/CDC changes) to auto-dispatch/prefetch 32-bit words every 32 TCKs inside a single continuous `Shift-DR` scan, eliminating per-word TAP transitions and USB-MPSSE packet framing.

---

## 2. Vivado 2018.2 OOC Synthesis Overhead (`my_soc_top @ 50 MHz + 10 MHz TCK`, `XC7VX485T`)

| Metric | ABI 5.0 Baseline | ABI 6.0 (EE + BitNet/INT2 + Stream) | Delta |
| --- | ---: | ---: | ---: |
| Slice LUTs | `31,621` | `32,150` | **`+529` (`+1.67%`)** |
| Slice Registers (FF) | `36,524` | `36,723` | **`+199` (`+0.54%`)** |
| Block RAM Tile | `4` | `4` | **`+0`** |
| DSPs | `82` | `83` | `+1` |
| Setup WNS (`@50 MHz`) | `+9.404 ns` | **`+9.054 ns`** | `-0.350 ns` |
| Hold WHS | `+0.096 ns` | **`+0.096 ns`** | **`0.000 ns`** |
| Top-Level Signal Pins | `7` | `7` | **`+0`** |

---

## 3. Verification Receipts (Icarus Verilog)
- **ABI 6.0 `OP_EXIT` Suite** (`hardware/npu/scripts/test_abi6_early_exit.py`): **PASS** (INT32 early-exit taken `131` cycles vs. fallthrough `286` cycles = `-54.2%` cycles on same `model.bin`; odd-channel `C=5` `u4` tail-padding; conditional forward jump; 3/3 negative rejections at `MODEL_COMMIT`).
- **BitNet-1.58b / INT2 Mixed-Precision Suite** (`hardware/npu/scripts/test_abi6_w2_bitnet.py`): **PASS** (`t2`/`i2`/`s2` Conv + DWConv + FC 0-bit exact; `model.bin` `1,808 B → 1,164 B`; combined `t2 + Exit + s2 + i2` in one `model.bin`; negative `w_mode` rejection).
- **Physical-Pin JTAG Fast-Stream** (`hardware/npu/tb/tb_jtag_stream.sv`): **PASS** (`64/64` words 0-bit exact via single `IR=15h` / `IR=16h` scan, zero overrun).
- **ABI 5.0 Regression & Unit Equivalence**: **74/74 PASS** (`run_program_regression.py --quick --skip-unit`) + `tb_npu_tensor_lookup` **PASS**.

---

## 4. CIFAR-10 ResNet-20 Early-Exit Pareto Evaluation (10,000 Test Samples)
- **Frozen PTQ Backbone** (`early_exit_pareto.json`, baseline `79.11%`): at `(th1=40, th2=28)`, `31.7%` exit early with **higher accuracy (`79.63%`, `+0.52%`)** and **`+9.7%` cycle savings** (`VC707 XDMA 246.33 ms → 219.11 ms`, `1.12×`).
- **Joint Multi-Exit INT4 QAT + FP32 Teacher KD** (`early_exit_joint_qat_pareto.json`, full accuracy boosted to **`90.61%`**):
  - At **`90.02%`** (`-0.59%`): `33.5%` exit at Stage-2 (`+7.5%` cycle savings, `222.22 ms`).
  - At **`88.27%`** (`+9.16%` above old PTQ): `45.2%` exit early (`+13.3%` cycle savings, `206.21 ms`, `1.19×`).
  - At **`79.06%`** (iso-accuracy vs. old PTQ): `74.7%` exit early (**`+27.8%` cycle savings**, **`166.22 ms`**, **`1.48×`**).
