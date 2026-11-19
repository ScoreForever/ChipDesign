# Generic ONNX Compiler & Weight-Stationary 3D Tiling Validation — ResNet-20 & ResNet-18 (2026-11-20)

This directory archives Zero-PDK, lightweight JSON validation receipts for the generic ONNX frontend and weight-stationary 3D tiling compiler (`software/compiler/`, `software/zoo/train_resnet.py`, targeting frozen ABI 5.0 with zero RTL modifications).

> **Scope note:** All results in this archive come from GPU/CPU exact-integer evaluation and Icarus Verilog RTL simulation (`iverilog` + `tb_npu_program.sv`). Real-board EGo1 FPGA execution is **not** claimed in this archive and remains pending Vivado bitstream build and hardware bring-up.

## 1. Benchmark Summary (Directly Verified Against Archived JSON Receipts)

| Model (`model`) | Params (`params`) | FP32 Teacher Top-1 | QAT / Distill Fake-Quant Top-1 | **Exact Integer ABI 5.0 Graph Top-1** | Eval Samples (`eval_samples`) | Program Groups / Tile Calls | Fused Subgraphs | Host Post-Ops | Total JTAG Bytes / Frame | Reference Bit-Exact Check |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| `resnet20_cifar10` | `272,474` | `92.63%` | `91.18%` | **`91.06%`** | `10,000` | `33` / `39` (`1.18×`) | `10` | `18` | `402,096 B` (`392.7 KiB`) | `5/5` frames (`all_frames_bit_exact: true`) |
| `resnet18_imagenet1k` | `11,689,512` | `78.14%` (1K-head) / `99.03%` (10-class) | `82.32%` (1K-head) | **`81.91%`** (1K-head) / **`97.63%`** (10-class) | `3,925` | `878` / `3,141` (`3.58×`) | `0` | `27` | `25,237,669 B` (`24.07 MiB`, model `5,958,432 B`) | `2/2` frames (`all_frames_bit_exact: true`) |

### Methodological Disclosures

1. **Fake-Quant vs. Exact Integer Accuracy**: In `software/zoo/train_resnet.py`, QAT/distillation evaluates fake-quantized PyTorch weights/activations with floating-point biases, whereas exported exact integer graph evaluation uses quantized integer weights, clamped integer biases, `Q15` per-channel requantization, and 32-bit signed wrapping. Both accuracies are reported separately above (exact-integer delta vs. fake-quant: **`-0.12` percentage points** on `resnet20_cifar10`, **`-0.41` percentage points** on `resnet18_imagenet1k` 1K-head).
2. **ResNet-18 1000-Class Head vs. 10-Class Subset**: Per `software/zoo/train_resnet.py`, `resnet18_imagenet1k` retains the 1000-class `IMAGENET1K_V1` classifier head, is distilled on Imagenette2-320 train against the frozen FP32 teacher's 1000-class logits (run invocation `--qat-epochs 18`; training logs are not stored in the JSON receipts), and is evaluated on the `3,925`-sample Imagenette2-320 validation split. Because distillation adapts quantization scales and weights on the Imagenette domain, the 1000-class-head accuracy on Imagenette (`81.91%`) exceeds the unadapted FP32 baseline (`78.14%`); the 10-class subset accuracy (`97.63%` vs. FP32 `99.03%`, **`-1.40` percentage points**) is reported alongside it.
3. **Host Post-Ops**: As recorded in `plan_summary.host_post_ops` (`18` for `resnet20_cifar10`, `27` for `resnet18_imagenet1k`), cross-tile residual additions, `K`-tile partial-sum reductions, and cross-tile requantizations run on the host via `TiledRuntime`, while the compiled ABI 5.0 program groups (`33` and `878`, respectively) run on the NPU backend.

## 2. RTL Simulation (`iverilog` + `tb_npu_program.sv`) Receipts

RTL verification runs compiled program groups through `RtlBackend` (`software/compiler/runtime.py`), which drives `tb_npu_program.sv` and checks final output equality against `oracle_reference`.

### 2.1 Full `resnet20_cifar10` Frame-0 End-to-End RTL Run (`resnet20_rtl_frame0_verify.json`)

- **Sample**: `sample_index: 0`, `true_label: 3` (matching `resnet20_golden_frames.json` entry `0`: `predicted: 3`, raw INT32 logit `439`).
- **Scope**: All `33` program groups (`model_loads: 33`) and `39` tile calls (`tile_infers: 39`).
- **Verification**: `passed: true`, `oracle_vs_tiled_ref_exact: true`, `oracle_vs_rtl_exact: true`, `max_abs_diff: 0`, `output_shape: [1, 1, 10]`.
- **RTL-Simulated NPU Cycles**: **`887,696` cycles** (`rtl_telemetry.npu_cycles`). At an assumed `50 MHz` clock, this projects to **`17.75 ms`** of pure NPU compute latency (derived projection from RTL-simulated cycles, not a measured FPGA/silicon latency).

### 2.2 `resnet18_imagenet1k` Representative Tile RTL Probes (`resnet18_rtl_probes_verify.json`)

| Probe | `program_id` | `layer_name` | `ops` | `input_shape` → `output_shape` | `input_dtype` → `output_dtype` | `model_bytes` | `oc_slice` / `k_slice` | `rtl_npu_cycles` (RTL-simulated) | `rtl_bit_exact` |
| --- | --- | --- | --- | --- | --- | ---: | --- | ---: | --- |
| `stem_7x7` | `p0000` | `stem` | `Conv`, `Requant` | `[39, 37, 3]` → `[17, 16, 64]` | `i4 → u4` | `5,184 B` | `[0, 64]` / `[0, 3]` | `103,084` | `true` |
| `maxpool_3x3` | `p0001` | `maxpool` | `MaxPool` | `[3, 113, 64]` → `[1, 56, 64]` | `u4 → u4` | `64 B` | `[0, 64]` / `null` | `9,141` | `true` |
| `ktiled_512_i32` | `p0022` | `s2_b0_c2` | `Conv` | `[9, 9, 16]` → `[7, 7, 16]` | `u4 → i32` | `1,280 B` | `[0, 16]` / `[112, 128]` | `3,285` | `true` |
| `fc_1000_oc_tile` | `p0836` | `fc` | `FC` | `[1, 1, 512]` → `[1, 1, 24]` | `u4 → i32` | `6,304 B` | `[0, 24]` / `[0, 512]` | `2,465` | `true` |

## 3. Archived Files

- `resnet20_eval_summary.json` — `resnet20_cifar10` accuracy (`10,000` samples) and `TiledPlan` summary.
- `resnet20_golden_frames.json` — `5` verified `resnet20_cifar10` golden frames (`bit_exact: true`).
- `resnet20_rtl_frame0_verify.json` — Full `resnet20_cifar10` Frame-0 RTL verification receipt (`887,696` RTL-simulated cycles).
- `resnet18_eval_summary.json` — `resnet18_imagenet1k` accuracy (`3,925` samples) and `TiledPlan` summary.
- `resnet18_golden_frames.json` — `2` verified `resnet18_imagenet1k` golden frames (`top1_pred`, `top5_pred`, `bit_exact: true`).
- `resnet18_rtl_probes_verify.json` — `4` representative `resnet18_imagenet1k` tile RTL probe receipts.

Large binary checkpoints (`.pt`, `.onnx`, and compiled binary `tiled_plan` weights) are excluded from git and regenerated via `software/zoo/train_resnet.py`.
