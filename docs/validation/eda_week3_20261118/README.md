# Week 3 Full-Stack ASIC Backend, 4-Corner MMMC, and Windowed VCD Power Report (2026-11-18)

**Baseline Ref**: `ac8f9b3` (`main` after PR #19: SRAM-backed `npu_tensor_lookup` & host JTAG single-RPC optimization) + external Week 3 scripts ([hardware/asic/week3/](../../../hardware/asic/week3/)).  
**Frozen Baseline Directory**: [`hardware/asic/optimize/`](../../../hardware/asic/optimize/) was **not modified** (`44 / 44` flow unit tests passing).  
**Build Profile** ([`build_profile.json`](build_profile.json)): `8×8` MAC array, INT4 activations (`4`-bit), INT4 weights (`4`-bit), `requant_fraction_bits = 15`, `clock_gating = true`.  
**Selected P&R Candidate** ([`experiment.json`](experiment.json)): `diag70_h25` (`diagonal_facing` dual-macro floorplan, configured `utilization = 0.70`, `halo = 26.0 µm`, `hold_target = 0.25 ns`, `congestion = true`).  
**Constraints**: Core `20.0 ns` (`50 MHz`), `TCK = 1000 ns` (`1 MHz`), nominal `TT` (`1.8 V` core / `3.3 V` IO / `25 °C`).

> [!IMPORTANT]
> **Verification Scope & Honest Boundaries**:
> This directory documents course/prototype backend closure and post-route analysis. It **does not** constitute foundry tape-out readiness:
> 1. **RC Extraction**: MMMC timing uses a single `typical.captable` scaled by corner temperature/library (`Typical captable only, temperature scaled; no RC process corners`).
> 2. **Physical Signoff Scope**: Calibre checks run on the standalone die (`TT` corner, `M5/M6` fill only), excluding Lab 5 seal ring, lower-layer dummy fill, IR/EM, and manual wire-bond/ERC/ESD signoff sheets.
> 3. **Calibre Course-Policy Waivers**: Raw Calibre DRC reports `2,427` items (exclusively inside vendor IO cells plus the unconditional `DRM.R.1` reminder; `0` blocking core violations), and Calibre LVS reports `INCORRECT` with `45` IO-resistor property mismatches up to `3.47%` (from course `270a` SPICE vs `280a` GDS IO library versions, accepted under the `≤ 5%` course policy).
> 4. **Zero-PDK Repository Policy**: All proprietary PDK views (`.lib`, `.db`, `.lef`, `.gds.gz`, `.spi`, `.captable`), post-route netlists (`mapped.v`, `physical.v`, `simulation.v`), `.def`, `.spef`, `.sdf`, and `.vcd` waveforms remain only on the course Linux server and are **never** committed to git. Exact input/output SHA-256 checksums are recorded in [`manifest.json`](manifest.json).

---

## 1. Executive Delta vs. `eda_latest_20261111` Baseline

| Metric | `2026-11-11` (`aa77887`, `diag75_drvfix2`) | `2026-11-18` (`ac8f9b3`, `diag70_h25`) | Delta / Change | Source |
| --- | ---: | ---: | ---: | --- |
| **Synthesis Std Cell Area (µm²)** | 1,394,641.29 | 1,167,773.96 | -226,867.33 (-16.27%) | [`synthesis_area_breakdown.json`](synthesis_area_breakdown.json) |
| **Synthesis Flop Count / Area (µm²)** | 16,411 / 734,314.16 | 12,065 / 553,133.32 | -4,346 (-26.48%) / -181,180.83 | [`synthesis_cells.json`](synthesis_cells.json) |
| **Synthesis Comb Area (µm²)** | 660,327.14 | 614,640.63 | -45,686.50 (-6.92%) | [`synthesis_area_breakdown.json`](synthesis_area_breakdown.json) |
| **`npu_tensor_lookup` Total Area (µm²)** | 231,352.14 (`4,433` FFs) | 8,811.54 (`87` FFs) | -222,540.60 (**-96.19%**) | [`synthesis_area_breakdown.json`](synthesis_area_breakdown.json) |
| **Macro Area (`2× RA1SHD_2048x32M8`) (µm²)** | 802,887.18 | 802,887.18 | 0.00 (0.00%) | [`metrics.json`](metrics.json) |
| **Post-Route Active Std Area (µm²)** | 1,431,456.99 (`63,248` inst) | 1,246,926.28 (`59,606` inst) | -184,530.71 (-12.89%) | [`physical_area_breakdown.json`](physical_area_breakdown.json) |
| **Core / Die Area (mm²)** | 2.690387 / 3.765152 | 2.624659 / 3.687322 | -2.44% / -2.07% | [`metrics.json`](metrics.json) |
| **Total Routed Wirelength (µm)** | 5,019,798.02 (5.020 m) | 3,852,916.17 (3.853 m) | -1,166,881.85 (**-23.25%**) | [`metrics.json`](metrics.json) |
| **Innovus TT Setup / Hold WNS (ns)** | +0.486 / +0.050 | **+0.628 / +0.231** | +0.142 / +0.181 (`TNS = 0.0`) | [`metrics.json`](metrics.json) |
| **Vectorless Total Power @ TT 50 MHz (mW)** | 63.403 | **59.859** | -3.544 (**-5.59%**) | [`power_summary.json`](power_summary.json) |

Replacing the 129-entry flip-flop register array inside `npu_tensor_lookup` with the SRAM-backed lookup table (PR #19) eliminated `4,346` flip-flops (`-181,106.20 µm²` register area and `-41,434.40 µm²` lookup combinational muxing = `-222,540.60 µm²` total), which directly enabled tighter routing (`-23.25%` wirelength) and a stronger `0.25 ns` hold-repair margin while shrinking die footprint (`3.687322 mm²`). Because `diag70_h25` tightened `hold_target` from `0.12 ns` (`11-11`) to `0.25 ns`, post-CTS hold/repair buffers added `69,903.95 µm²` (`6,690` instances, vs. `23,890.36 µm²` on `11-11`), leaving post-route active combinational area at `693,773.20 µm²` while delivering `+0.231 ns` TT hold slack.

---

## 2. Synthesis, Module Breakdown, and Physical Layout Verification

### 2.1 Synthesis Summary ([`synthesis_cells.json`](synthesis_cells.json), [`synthesis_area_breakdown.json`](synthesis_area_breakdown.json))
- **Total Library Cell Area (`library_cell_area_um2`)**: `1,970,661.14 µm²` (`51,905` mapped leaf instances, `0` latches).
- **Clock Gating (`CKLNQD1BWP7T`)**: `265` integrated clock-gating cells (`12,065` sequential elements total vs. `16,411` and `394` ICGs on `11-11`).

### 2.2 Post-Route Active Area by Hierarchy ([`physical_area_modules.csv`](physical_area_modules.csv))

| Module Group | Instance Count | Active Area (µm²) | Sequential Count | Seq Area (µm²) | Comb Area (µm²) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `NPU` | 46,573 | 1,000,495.33 | 9,905 | 452,830.25 | 547,665.08 |
| `shared_store_logic` | 8,670 | 159,066.39 | 1,289 | 60,014.57 | 99,051.81 |
| `controller` | 3,367 | 62,323.92 | 518 | 23,157.16 | 39,166.76 |
| `host` | 918 | 24,349.16 | 353 | 17,151.10 | 7,198.06 |
| `backend_or_unattributed` | 78 | 691.49 | 0 | 0.00 | 691.49 |
| **Total Active Std Cells** | **59,606** | **1,246,926.28** | **12,065** | **553,153.08** | **693,773.20** |

### 2.3 Physical Checks & Layout Renders ([`checks.json`](checks.json), [`macro_pins.tsv`](macro_pins.tsv), [`images/`](images/))
- **All 11 Innovus Geometry/Physical Checks (`checks.json`)**: `antenna`, `cdc`, `connectivity`, `drc`, `drv`, `macro_pins`, `pad_geometry`, `pg_connectivity`, `pg_drc`, `placement`, `ports` — all `true`.
- **SRAM Macro Orientation (`macro_pins.tsv`)**: `g_bank[1].i_sram` (`model`, `R90`, pins `E`), `g_bank[0].i_sram` (`activation`, `R270`, pins `W`); both `64 / 64` pins routed cleanly with center offsets `±0.493 µm`.
- **Layout Views (`images/`)**:
  - [`1_placement.png`](images/1_placement.png), [`2_power.png`](images/2_power.png), [`3_signal_routing.png`](images/3_signal_routing.png), [`5_all.png`](images/5_all.png) (full-chip diagonal-facing dual-SRAM floorplan, power rings/stripes, and M1–M6 routing).
  - [`4a_m56_no_fill.png`](images/4a_m56_no_fill.png) vs. [`4b_m56_with_fill.png`](images/4b_m56_with_fill.png) (`M5`/`M6` metal fill verification).
  - [`6_corner_zoom.png`](images/6_corner_zoom.png) & [`7_sram_diag_zoom.png`](images/7_sram_diag_zoom.png) (IO corner & diagonal SRAM halo channel detail).

---

## 3. 4-Engine Independent Signoff & Gate-Level SDF Simulation

Sources: [`signoff_summary.json`](signoff_summary.json), [`sdf_result.json`](sdf_result.json).

| Engine | Tool / Scope | Status (`passed`) | Key Result & Raw Details |
| --- | --- | :---: | --- |
| **STA** | PrimeTime (`TT`, std cells + IO) | `true` | Setup WNS = **+1.368277 ns**, Hold WNS = **+0.23465 ns**, `unannotated = false` (`RA1SHD_2048x32M8` black-box interface verified in Innovus below). |
| **LEC** | Formality (RTL vs. routed `physical.v`) | `true` | `verified = true`, `succeeded = true`. |
| **DRC** | Calibre (`chip.gds.gz`, standalone die) | `true` | `blocking = {}`; `2,427` accepted IO-ring / unconditional deck items (`ESD.25g: 2384`, `ESD.24g: 30`, `LUP.5.3g_3.3V: 10`, `LUP.3.3g_3.3V: 1`, `CO.R.4: 1`, `DRM.R.1: 1`). |
| **LVS** | Calibre (GDS vs. SPICE) | `true` | Raw `incorrect = true` with `45` IO-resistor property deviations (`max = 3.47%` ≤ `5%` course policy from `270a`/`280a` IO library mismatch). |
| **SDF Gate Sim** | Xcelium + `routed.sdf` (`NTC` / `RECREM`) | `true` | `41` JTAG commands; `5` accepted time-0 input pad conflicts; `6` accepted SRAM negedge period msgs; `50,868` accepted library `ifnone` arcs; `0` fatal or unexpected `SDFNEP`/`SDFNET`. |

---

## 4. 4-Corner MMMC Timing Closure & CDC Audit

Sources: [`mmmc_summary.json`](mmmc_summary.json), [`mmmc_corners.csv`](mmmc_corners.csv), [`sram_interface_timing.csv`](sram_interface_timing.csv), [`cdc_4corners.tsv`](cdc_4corners.tsv).  
Extracted via [`run_mmmc_closure.py`](../../../hardware/asic/week3/run_mmmc_closure.py) and [`mmmc_sta.tcl`](../../../hardware/asic/week3/mmmc_sta.tcl) using post-route `def`, `spef`, and `mapped.sdc` (`Typical captable only, temperature scaled; no RC process corners`).

### 4.1 Corner Setup & Hold Summary (`mmmc_corners.csv`)

| Corner | Period (ns) | Mode | WNS (ns) | TNS (2000-ep cap) | Violating EP | Critical Start -> End Path |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| `ss125` | 20.0 (50 MHz) | `setup` | **-16.772** | -13,337.958 | 2,000 | `i_control/i_npu/d_reg[0][0]/Q` -> `.../g_bank[0].i_sram/WEN[0]` |
| `ss125` | 20.0 (50 MHz) | `hold` | **+0.458** | 0 | 0 | `i_control/i_npu/i_requant/out_data_reg[3]/Q` -> `i_control/i_npu/write_data_reg[5][3]/D` |
| `SS125_DERATED` | **38.0 (26.32 MHz)** | `setup` | **+0.234** | — | — | `i_host/ack_toggle_reg/Q` -> `i_host/ack_sync_reg[0]/D` |
| `SS125_DERATED` | **38.0 (26.32 MHz)** | `hold` | **+0.458** | — | — | `i_control/i_npu/i_requant/out_data_reg[3]/Q` -> `i_control/i_npu/write_data_reg[5][3]/D` |
| `tt25` | 20.0 (50 MHz) | `setup` | **+0.628** | 0 | 0 | `i_control/i_npu/d_reg[0][0]/Q` -> `.../i_store_cache_data_reg[7][7]/D` |
| `tt25` | 20.0 (50 MHz) | `hold` | **+0.231** | 0 | 0 | `.../i_store_cache_data_reg[15][22]/Q` -> `.../i_store_high_word_reg[22]/D` |
| `ff0` | 20.0 (50 MHz) | `setup` | **+4.568** | 0 | 0 | `i_control/i_npu/d_reg[0][0]/Q` -> `.../i_store_cache_data_reg[7][7]/D` |
| `ff0` | 20.0 (50 MHz) | `hold` | **+0.097** | 0 | 0 | `.../i_store_cache_data_reg[12][22]/Q` -> `.../i_store_high_word_reg[22]/D` |
| `ffm40` | 20.0 (50 MHz) | `setup` | **+5.363** | 0 | 0 | `i_control/i_npu/d_reg[0][0]/Q` -> `.../i_store_cache_data_reg[7][7]/D` |
| `ffm40` | 20.0 (50 MHz) | `hold` | **+0.081** | 0 | 0 | `i_control/i_npu/d_reg[2][0]/Q` -> `i_control/i_npu/element_addr_reg[1]/SI` |

- **MMMC Signoff Observation**: At worst-case slow-slow (`1.62 V, 125 °C`), `50 MHz` has `-16.772 ns` setup slack dominated by SRAM macro write-path latency (`32.041 ns` arrival vs `15.268 ns` required); derating `core_clk` to **`38.0 ns` (`26.32 MHz`)** closes `SS125_DERATED` cleanly (`+0.234 ns` setup / `+0.458 ns` hold). Across `tt25`, `ff0`, and `ffm40`, `50 MHz` closes both setup and hold (`+0.081 ns` worst hold margin at `ffm40`).

### 4.2 SRAM Macro Interface Timing & 4-Corner CDC Check
- **SRAM Interface Timing ([`sram_interface_timing.csv`](sram_interface_timing.csv))**: `8 / 8` role-corner measurements populated (`0` NAs):
  - `tt25`: Bank 0 (`activation`) `+0.788 / +0.546 ns` (`to`), `+4.449 / +1.636 ns` (`from`); Bank 1 (`model`) `+0.895 / +0.658 ns` (`to`), `+4.375 / +1.713 ns` (`from`).
  - `ff0` / `ffm40`: Setup `+4.779 .. +5.587 ns` (`to`) / `+9.134 .. +9.676 ns` (`from`); Hold `+0.272 .. +0.392 ns` (`to`) / `+0.898 .. +0.993 ns` (`from`).
  - `ss125` (`20 ns`): Setup `-16.772 ns` (`activation`) / `-15.970 ns` (`model`) (`to`), `-7.163` / `-7.329 ns` (`from`); Hold `+1.074 .. +3.376 ns`.
- **4-Corner JTAG-to-Core CDC ([`cdc_4corners.tsv`](cdc_4corners.tsv))**: `452` explicit path checks (`113` CDC paths × `4` corners) across `request` (`host -> sync`), `payload` (`host -> core`), and `ack` (`core -> host`) groups. **Min slack across all 452 checks is positive**: `ffm40 = +0.298 ns`, `ff0 = +0.337 ns`, `tt25 = +0.551 ns`, `ss125 = +1.018 ns`.

---

## 5. Windowed Gate-Level VCD Power & Energy Analysis (`busy -> irq`)

Sources: [`power_summary.json`](power_summary.json), [`power_workloads.csv`](power_workloads.csv).  
For each workload, Xcelium ran post-route SDF-annotated simulation ([`tb_fullchip_power.sv`](../../../hardware/asic/week3/tb_fullchip_power.sv)) dumping VCD strictly over the NPU execution window (`busy_ns -> irq_ns`, resolved hierarchically via `i_control.busy` -> top-level `irq_o`), followed by `Innovus Voltus` `report_power` (`TT`, `50 MHz`). Every workload achieved **`100.0%` net VCD activity annotation** (`62,790 / 62,790` nets). All `.vcd` waveforms were hashed (SHA-256) and deleted immediately afterward to preserve disk/Zero-PDK rules.

| Workload | NPU Cycles | Window `dt` (ns) | Total Power (mW) | Internal / Switching / Leakage (mW) | Macro / Seq / Comb / IO / Clk (mW) | Zero-Toggle (%) | Energy (µJ) |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `vectorless` (default) | — | — | 59.859 | 44.114 / 15.708 / 0.0368 | 2.116 / 31.710 / 18.970 / 0.874 / 6.187 | — | — |
| `cnn_f1` (fused) | 515 | 10,321.856 | **29.427** | 22.189 / 7.202 / 0.0369 | 3.430 / 14.770 / 4.921 / 1.887 / 4.424 | 30.944% | **0.3037** |
| `cnn_f0` (unfused) | 1,381 | 27,641.856 | **24.961** | 17.916 / 7.008 / 0.0369 | 4.166 / 9.912 / 5.284 / 1.884 / 3.716 | 25.214% | **0.6900** |
| `ecg` | 13,247 | 264,961.856 | **44.532** | 34.638 / 9.857 / 0.0369 | 5.711 / 21.070 / 8.329 / 3.874 / 5.547 | 0.073% | **11.799** |
| `har` | 15,357 | 307,161.856 | **47.419** | 37.047 / 10.335 / 0.0369 | 5.982 / 25.470 / 7.924 / 1.866 / 6.179 | 23.727% | **14.565** |
| `mnist_l` | 30,184 | 603,701.856 | **51.049** | 39.771 / 11.240 / 0.0369 | 5.540 / 27.840 / 9.218 / 1.857 / 6.595 | 20.035% | **30.818** |

- **Operator Fusion Energy Impact (`cnn_f1` vs. `cnn_f0`)**: Fused execution reduces NPU inference latency by `62.71%` (`515` vs. `1,381` cycles) and total energy per inference by **`55.98%`** (**`0.3037 µJ`** vs. **`0.6900 µJ`**). Vectorless analysis (`59.859 mW`) overestimates real workload power by `1.17×–2.40×` (`24.961–51.049 mW`).

---

## 6. Reproducibility ([`stage_receipts.json`](stage_receipts.json), [`hardware/asic/week3/`](../../../hardware/asic/week3/))

Execution receipts and wall-clock runtimes (`synth` `1067s`, `trial diag70_h25` `1513s`, `postlayout` `65s`, `signoff` `153s`, `mmmc` `2041s`, `power` `1336s`, `render_views` `72s`, `screenshots` `31s`) are recorded in [`stage_receipts.json`](stage_receipts.json). To re-run the entire Week 3 pipeline on the course Linux EDA host:

```bash
source /lec/eda.env
python3 hardware/asic/week3/run_week3_eda.py --out-dir /path/to/eda_week3_run
```

Standalone post-route stages can also be invoked independently on a finished candidate directory:

```bash
python3 hardware/asic/week3/run_mmmc_closure.py --candidate <cand_dir> --out-dir <mmmc_out>
python3 hardware/asic/week3/measure_power.py --candidate <cand_dir> --out-dir <power_out>
```
