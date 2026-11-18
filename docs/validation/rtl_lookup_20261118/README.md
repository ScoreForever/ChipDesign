# PR #15 (`area/lookup-sram`) Seven-Model RTL Comparison (2026-11-18)

- **Commits compared**: `a661b46` (flip-flop provenance table in `npu_tensor_lookup`) vs `8dd2626` (PR #15: provenance stored in activation SRAM `[0, 4*(op_count+1))` during `MODEL_COMMIT`, reused at `START`).
- **Configuration & verification scope**: Executed both matched memory models (`behavioral` default and `CHIPDESIGN_FPGA_MEM`) via `run_program_regression.py --quick --skip-unit --packaged-models` under `Icarus Verilog version 13.0 (stable) (v13_0)` (`8x8` array), verifying bit-exact `writes.hex`, 24 performance counters, operator trace, cold + warm inference runs, and 19 negative compiler/lifecycle checks on all 4 commit×memory runs.
- **Matched-baseline reconciliation vs PR #15 summary**: GitHub PR #15's summary listed `ecg` `13,989 -> 13,247` (`-5.30%`), mixing `CHIPDESIGN_FPGA_MEM` before (`13,989`) with `behavioral` after (`13,247`). Under matched memory configurations, cold-run cycle reductions are identical in absolute cycles across both (`-35` to `-195` cycles, `-0.10%` to `-1.45%`).
- **Deferred / not re-run**: The full 85-program / 81-mutant CI sweep was not re-run locally; PR #15's unverified Yosys generic `4,433 -> 87` FF claim is cited from PR #15's GitHub description, while authoritative **Genus 19.17 TSMC 180nm** area measurement is **deferred to PR 3**.

## Matched `behavioral` vs `CHIPDESIGN_FPGA_MEM` Cold/Warm Cycles

| Memory Config | Model | Bytes | Peak (B) | Cold Cycles (`a661b46` -> `8dd2626`) | Cold Delta (%) | Warm Cycles (`a661b46` -> `8dd2626`) | Checked Writes |
|---|---|---:|---:|---:|---:|---:|---:|
| `behavioral` | `ecg` | 3908 | 1600 | 13442 -> 13247 | -195 (-1.45%) | 13442 -> 13247 (-195) | 10794 |
| `behavioral` | `fashion_cnn` | 8104 | 2744 | 35800 -> 35765 | -35 (-0.10%) | 35800 -> 35765 (-35) | 6324 |
| `behavioral` | `gtsrb_l` | 7736 | 8192 | 79110 -> 78990 | -120 (-0.15%) | 79110 -> 78990 (-120) | 28686 |
| `behavioral` | `har` | 4216 | 2624 | 15426 -> 15357 | -69 (-0.45%) | 15421 -> 15358 (-63) | 7244 |
| `behavioral` | `kws_l` | 7896 | 6248 | 71420 -> 71318 | -102 (-0.14%) | 71417 -> 71318 (-99) | 42072 |
| `behavioral` | `kws_m` | 7632 | 5248 | 73081 -> 72935 | -146 (-0.20%) | 73078 -> 72935 (-143) | 45064 |
| `behavioral` | `mnist_l` | 4688 | 3528 | 30304 -> 30184 | -120 (-0.40%) | 30304 -> 30184 (-120) | 17332 |
| `fpga_mem` | `ecg` | 3908 | 1600 | 13989 -> 13794 | -195 (-1.39%) | 13989 -> 13794 (-195) | 10794 |
| `fpga_mem` | `fashion_cnn` | 8104 | 2744 | 36016 -> 35981 | -35 (-0.10%) | 36016 -> 35981 (-35) | 6324 |
| `fpga_mem` | `gtsrb_l` | 7736 | 8192 | 79756 -> 79636 | -120 (-0.15%) | 79756 -> 79636 (-120) | 28686 |
| `fpga_mem` | `har` | 4216 | 2624 | 15826 -> 15757 | -69 (-0.44%) | 15821 -> 15758 (-63) | 7244 |
| `fpga_mem` | `kws_l` | 7896 | 6248 | 72006 -> 71904 | -102 (-0.14%) | 72003 -> 71904 (-99) | 42072 |
| `fpga_mem` | `kws_m` | 7632 | 5248 | 73716 -> 73570 | -146 (-0.20%) | 73713 -> 73570 (-143) | 45064 |
| `fpga_mem` | `mnist_l` | 4688 | 3528 | 30836 -> 30716 | -120 (-0.39%) | 30836 -> 30716 (-120) | 17332 |
