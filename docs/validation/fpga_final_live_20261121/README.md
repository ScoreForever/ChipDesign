# Live `57.143 MHz` EGo1 FPGA & Co-Optimization Benchmark Archive

- **Hardware Platform**: Digilent/EGo1 FPGA (`XC7A35T`, NPU system clock **`57.143 MHz`** with `CHIPDESIGN_FPGA_MEM` synchronous BRAM enabled), connected via external FT232H USB-to-JTAG MPSSE bridge (`0403:6014`, `usbipd-win` pass-through to WSL2)
- **Tested Commits & Provenance**:
  - `models/` & `scenes/` suites: recorded on commit `8610c7f` (identical `57.143 MHz` bitstream; all outputs `<= 172 B`, well below the FT232H RX FIFO threshold)
  - `resnet20/`, `resnet18/`, & `rtl_audit/`: recorded on commit `0763111` (after PR #31 `FtdiMpsseTransport` `read_chunk_words=128` FIFO fix; functionally identical RTL)

## 1. Live 7-Model x 7-TCK MPSSE Frequency Sweep (`models/`)
All `7` zoo models (`ecg`, `har`, `mnist_l`, `fashion_cnn`, `kws_m`, `kws_l`, `gtsrb_l`) verified across `7` TCK frequencies (`1, 2, 3, 5, 8, 10, 15 MHz`), `17` inferences per `(model, TCK)` (`1` cold load + first infer + `16` warm resident inferences) = **100% 0-bit exact** (`passed = true`, `fpga_validated = true`).

| Model | `model_bytes` | `in / out` B | Ops | FPGA Cycles (`@57.143 MHz`) | Compute (`us`) | Behavioral RTL Cycles (`rtl_audit/`) | `@1 MHz` Load / Warm (`ms`) | `@10 MHz` Load / Warm (`ms`) | `@15 MHz` Load / Warm (`ms`) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ecg` | `3908` | `64 / 20` | `13` | `14571` | `254.99` | `14024` | `112.65 / 16.55` | `48.01 / 17.64` | `44.59 / 16.70` |
| `har` | `4216` | `576 / 24` | `7` | `16047` | `280.82` | `[15646, 15647]` | `99.83 / 23.61` | `55.83 / 21.77` | `51.43 / 22.61` |
| `mnist_l` | `4688` | `392 / 40` | `10` | `31782` | `556.18` | `31250` | `111.77 / 22.64` | `50.77 / 17.78` | `46.74 / 16.18` |
| `fashion_cnn` | `8104` | `392 / 40` | `5` | `36225` | `633.94` | `36009` | `149.60 / 21.86` | `55.93 / 17.75` | `48.77 / 17.77` |
| `kws_m` | `7632` | `245 / 48` | `11` | `75322` | `1318.13` | `74687` | `139.21 / 23.20` | `55.30 / 16.48` | `54.65 / 18.86` |
| `kws_l` | `7896` | `245 / 48` | `9` | `73706` | `1289.85` | `73120` | `151.29 / 27.38` | `51.14 / 17.23` | `52.89 / 17.66` |
| `gtsrb_l` | `7736` | `2048 / 172` | `10` | `80919` | `1416.08` | `80273` | `138.65 / 43.18` | `53.69 / 19.22` | `60.70 / 25.39` |

* **Hardware vs Behavioral Cycle Note**: FPGA BRAM (`CHIPDESIGN_FPGA_MEM`) incurs a 1-cycle synchronous read latency per tensor read relative to combinational/behavioral ASIC SRAM simulation in `rtl_audit/model_audit.json` (`112/112` golden frames across `7` models x `16` vectors verified in Icarus Verilog at commit `0763111`, `passed = true`). Both variants produce bit-identical outputs (`0`-bit diff).

## 2. Live 6-Scene Golden Verification (`scenes/`, `80/80` Frames, `tck = 10 MHz`)
| Scene | Model | Frames | Exact Match | `npu_cycles_median` | Median Latency (`ms`) | Mean Throughput (`fps`) |
|---|---|---:|---:|---:|---:|---:|
| `mnist` | `mnist_l` | `16` | `16/16` | `31782` | `18.6425` | `47.997` |
| `kws` | `kws_l` | `16` | `16/16` | `73706` | `17.9200` | `50.823` |
| `ecg` | `ecg` | `16` | `16/16` | `14571` | `20.4243` | `48.474` |
| `har` | `har` | `16` | `16/16` | `16047` | `18.0569` | `54.591` |
| `face` | `face` | `8` | `8/8` | `33710` | `18.8229` | `52.658` |
| `emotion` | `emotion` | `8` | `8/8` | `65771` | `17.6748` | `56.722` |
| **Total** | — | **`80`** | **`80/80` (`1.0`)** | — | — | — |

* **Scope Note (`scenes/scenes.json` `scope_note`)**: Representative golden inputs of the six demo scenes (`mnist_l/kws_l/ecg/har` 16 each, `face/emotion` 8 each) checking bit-exact output agreement (`agreement_rate = 1.0`), not dataset classification accuracy.

## 3. Live Deep Residual Networks (`resnet20/` & `resnet18/`, `tck = 10 MHz`)
Both models are compiled from quantized INT4 QAT graphs onto the unified `16,384 B` on-chip SRAM budget using ABI 5.0 co-optimization (`OP_ADD = 7` hardware residual tiling + fused Conv+MaxPool halo, documented in `coopt_summary.json`):

- **`ResNet-20` (CIFAR-10 INT4 QAT, `42` program groups, `51` tile calls, `19` fused subgraphs, `0` host post ops, `533,040 B` JTAG/frame)**:
  - **`samples_checked = 5/5`**, `bit_exact = true`, `max_abs_diff = 0`, `label_correct = 4/5`, `fpga_validated = true`
  - Hardware compute: `npu_cycles_median = 983,666` (**`17.214 ms`** active NPU compute `@57.143 MHz`)
  - End-to-end breakdown (median): `wall = 4264.02 ms`, `host = 4.63 ms`, `npu_compute = 17.21 ms`, `jtag_transfer = 4242.40 ms` (`99.49%` USB/JTAG transport + polling bound)
- **`ResNet-18` (Imagenette INT4 QAT, `855` program groups, `2,370` tile calls, `11` fused subgraphs, `11` host post ops, `24,357,357 B` planned JTAG vs. `24,354,093 B` measured live telemetry per frame)**:
  - **`samples_checked = 1` representative live frame**, `bit_exact = true`, `max_abs_diff = 0`, `label_correct = 1/1` (`label = 0, predicted = 0`), `fpga_validated = true`
  - Hardware compute: `npu_cycles_median = 46,504,887` (**`813.833 ms`** active NPU compute `@57.143 MHz`; `852` runtime `model_loads` / `5,980,800 B` due to model-residency reuse saving `3,264 B` vs. `5,984,064 B` planned across `855` groups, plus `2,370` `tile_infers` carrying `10,141,645 B` input + `8,231,648 B` output)
  - End-to-end breakdown: `wall = 150,572.66 ms` (`150.57 s`), `host = 347.02 ms`, `npu_compute = 813.83 ms`, `jtag_transfer = 149,411.80 ms` (`99.23%` USB/JTAG transport bound)
  - **Live Multi-Frame Scope Note (includes unarchived stress observations)**: `resnet18/resnet.json` archives the single-frame `passed = true, fpga_validated = true` receipt (`samples_checked = 1`). In two additional unarchived multi-frame stress runs (`--max-frames 2`, not committed in this directory), sample 0 also passed `bit_exact = true` (`147.92 s` and `160.38 s`, i.e., `3/3` sample-0 passes overall) before Windows/WSL USB-IP encountered a transient `UsbError: [Errno 110] Operation timed out` mid-sample-1 (>4 min sustained bulk transfers over Windows `usbipd-win`), while post-timeout `nocpu_jtag.py status` confirmed the active tile finished cleanly on the FPGA (`done = true, faulted = false, error = 0`). Therefore `resnet18/` reports `samples_checked = 1` honestly.
