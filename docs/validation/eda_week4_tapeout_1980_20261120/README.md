# Week 4 Strict `1980.000 × 1980.000 µm` ASIC Lab 4 + Lab 5 Tapeout Archive (`2026-11-20`)

This directory archives Zero-PDK signoff receipts, physical/power summaries, 12 high-resolution layout views, and the bilingual tapeout acceptance & Foundry ERC waiver memo for the strict **`1980.000 × 1980.000 µm`** (`3,960,000 × 3,960,000 DBU` at `2000 DBU/µm`) True-MMMC physical implementation of `my_soc_top` (zero RTL modifications; `hardware/asic/optimize/*` untouched).

> **Formal Signoff Status:** `formal_tapeout_signoff: false`. All timing, equivalence, dual-corner gate-level SDF, Calibre DRC (`0` blocking), non-blackbox nmLVS (`CORRECT` at `3.48%` poly-resistor tolerance), Antenna (`0/0`), WireBond (`0/0`), and delivery checksum self-checks passed; `7` hierarchical (`977` expanded) Calibre ERC markers in unmodified vendor IO cells and the exterior Seal Ring substrate moat remain open for written Foundry/TA waiver. See [tapeout_acceptance_and_erc_waiver.md](tapeout_acceptance_and_erc_waiver.md) for the complete bilingual signoff ledger and ERC waiver audit.

## 1. Key Physical, Timing & Signoff Results

| Domain | Check / Metric | Result | Receipt |
| --- | --- | --- | --- |
| **Die Geometry** | Sealed `postDummy.gds` outer size | **`1980.000 × 1980.000 µm`** (`[-15.32, -15.04, 1964.68, 1964.96]`, exact `max_allowed_sealed_die_um: 1980.0`) | `lab5_summary.json` |
| | Unsealed raw die / Core area | `1949.36 × 1949.92 µm` (`3.801096 mm²`) / `2.715243 mm²` (`[150.08, 151.76, 1799.28, 1798.16] µm`, `62,246` active stdcells + `2` SRAM macros) | `lab5_summary.json`, `floorplan_geometry.json`, `metrics.json` |
| **True-MMMC STA** | `ss125` setup / hold WNS | **`+0.533 ns`** / **`+0.592 ns`** (`0` violations) | `mmmc_corners.csv` |
| (Innovus, 4 PVT) | `tt25` setup / hold WNS | **`+7.965 ns`** / **`+0.306 ns`** (`0` violations) | `mmmc_corners.csv` |
| | `ff0` setup / hold WNS | **`+11.368 ns`** / **`+0.167 ns`** (`0` violations) | `mmmc_corners.csv` |
| | `ffm40` setup / hold WNS | **`+11.769 ns`** / **`+0.150 ns`** (`0` violations) | `mmmc_corners.csv` |
| **RC Sensitivity** | `ss125` `+15%` / `+20%` RC setup WNS | **`+0.236 ns`** / **`+0.340 ns`** (`0` violations; non-monotonic as measured) | `rc_sensitivity.tsv` |
| | `ffm40` `-15%` / `-20%` RC hold WNS | **`+0.144 ns`** / **`+0.141 ns`** (`0` violations) | `rc_sensitivity.tsv` |
| **SRAM & CDC** | Dual `RA1SHD_2048x32M8` interface (4 corners) | All `setup_to`/`hold_to`/`setup_from`/`hold_from` positive (`ss125` min `+0.577 ns`, `ffm40` min `+0.248 ns`) | `sram_interface_timing.csv` |
| | 4-corner CDC audit (`113 × 4`) | **`452 / 452` paths positive** (overall min slack `+0.292 ns` at `ffm40`) | `cdc_4corners.tsv` |
| **Independent STA / LEC** | PrimeTime STA (TT25 + Innovus SPEF) | **`setup_wns_ns: +8.782676`**, **`hold_wns_ns: +0.290397`**, `unannotated: false`, `passed: true` | `signoff_summary.json` |
| | Formality LEC (mapped vs. routed) | **`verified: true`**, **`succeeded: true`**, **`passed: true`** | `signoff_summary.json` |
| **Dual-Corner SDF** | `SS 125°C MAX` (`MAXIMUM`) | **`passed: true`**, `41` JTAG commands, `0` timing diagnostics, `0` `SDFNDP` | `dual_sdf_summary.json` |
| (Xcelium gate-level) | `FF -40°C MIN` (`MINIMUM`) | **`passed: true`**, `41` JTAG commands, `0` timing diagnostics, `120,327` `SDFNDP` clamped negative IOPATHs | `dual_sdf_summary.json` |
| **Lab 5 Calibre** | DRC (`523` rules, `postDummy.gds`) | **`0` blocking** (`passed: true`; `CO.R.4` PIMP fix verified; `2,427/16,935` accepted teaching guidelines) | `lab5_summary.json` |
| (Same-source) | nmLVS (`postDummy.gds` vs. `my_soc_top.cdl`) | **`CORRECT` @ `3.48%` poly-R tol** (`12/12` ports, `0` property errors, **`sram_blackboxed: false`**; `0%` tol has `45` IO 270a/280a resistor deltas max `3.47%`) | `lab5_summary.json` |
| | Antenna (`62` rules) / WireBond (`64` rules) | **`0 / 0`** (`passed: true`) / **`0 / 0`** (`passed: true`) | `lab5_summary.json` |
| | ERC (`12` rules) | `7` hier / `977` expanded (`manual_review_required`; `0` markers in active core/SRAM) | `lab5_summary.json`, `final_erc_geometry.json` |
| **Formal Delivery** | `design_and_reports.tar.gz` + `postDummy.gds` + `my_soc_top.cdl` + `final_inputs.sha256` | `gzip: true`, `sha256sums: true`, `final_inputs: true` (`120.2 MB` / `137.0 MB` / `14.1 MB` / `161 B`) | `delivery_manifest.json` |

## 2. 5-Workload 100%-VCD TT25 Dynamic Power (`power_summary.json`, `power_workloads.csv`)

Measured on `view_tt25` over exact gate-level inference windows (`busy↑` → `irq↑`) using `hardware/asic/week3/measure_power.py` (all `.vcd` files hashed to SHA-256 and deleted after power extraction):

| Workload | NPU Cycles | Window `dt` (ns) | VCD Annotation | Internal (mW) | Switching (mW) | Leakage (mW) | **Total Power (mW)** | Macro / SRAM (mW) | Clock (mW) | **Energy / Frame (µJ)** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `vectorless` (baseline) | — | — | `N/A` | `44.414` | `16.379` | `0.037` | **`60.829`** | `2.100` | `6.483` | — |
| `cnn_f1` (fused) | `515` | `10,322.80` | `100.0%` | `21.976` | `7.233` | `0.037` | **`29.247`** | `3.440` | `4.608` | **`0.3019`** |
| `cnn_f0` (unfused) | `1,381` | `27,642.80` | `100.0%` | `17.696` | `6.911` | `0.037` | **`24.644`** | `4.186` | `3.843` | **`0.6812`** |
| `ecg` | `13,247` | `264,962.80` | `100.0%` | `34.306` | `9.873` | `0.037` | **`44.216`** | `5.866` | `5.813` | **`11.7156`** |
| `har` | `15,357` | `307,162.80` | `100.0%` | `36.468` | `10.402` | `0.037` | **`46.906`** | `6.073` | `6.496` | **`14.4079`** |
| `mnist_l` | `30,184` | `603,702.80` | `100.0%` | `39.229` | `11.313` | `0.037` | **`50.580`** | `5.656` | `6.940` | **`30.5351`** |

- **Operator Fusion Energy Reduction (`cnn_f1` vs. `cnn_f0`)**: **`55.68%`** (`cnn_energy_savings_percent: 55.6817`).

## 3. Archived Visual Views (`images/`)

- **Innovus Post-Route Views (8 PNGs)**: `1_placement.png`, `2_power.png`, `3_signal_routing.png`, `4a_m56_no_fill.png`, `4b_m56_with_fill.png`, `5_all.png`, `6_corner_zoom.png`, `7_sram_diag_zoom.png`.
- **Lab 5 `postDummy.gds` Signoff Views (4 PNGs)**: `8_lab5_sealring_corner.png`, `9_lab5_dummy_fill_zoom.png`, `10_lab5_wirebond_keepout_zoom.png`, `11_lab5_cor4_and_erc_audit.png`.

## 4. Zero-PDK & Reproduction Scope

No GDS, CDL, DEF, LEF, SPEF, SDF, VCD, SVDB, vendor SPICE, or PDK rule decks are stored in git. Full physical reproduction scripts live under `hardware/asic/week3/` (`tapeout_1980_geometry.py`, `tapeout_1980_pnr.tcl`, `run_mmmc_tapeout_1980.py`, `run_lab5_signoff.py`, `package_lab5_delivery.py`, `measure_power.py`, `render_lab5_views.py`).
