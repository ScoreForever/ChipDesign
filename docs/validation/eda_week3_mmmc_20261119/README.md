# Week 3 Pure-Backend 4-Corner 50 MHz True-MMMC Closure & Unified Physical Signoff (`2026-11-19`)

## Scope & Single-GDS Provenance

This validation record documents the **zero-RTL, pure-backend 4-corner 50 MHz True-MMMC ASIC closure** (`SS 125°C` over-constrained high-effort synthesis at `18.0 ns`, re-constrained to `20.0 ns`, followed by joint 4-corner Innovus P&R + post-route MMMC ECO) and complete single-GDS physical verification (`postDummy.gds`) on the same SRAM-lookup ABI 5.0 `8×8` NPU architecture.

- **Architecture**: ABI 5.0 `8×8` systolic NPU (`INT4×INT4`, `requant_fraction_bits = 15`, `clock_gating = true`), 2 × `RA1SHD_2048x32M8` SRAM macros (`diagonal_facing`), `npu_tensor_lookup` migrated into activation SRAM, 35 × `PAD60LU_OBV` CUP bond pads (`3 µm` M5/M6 route & fill keepout active before routing).
- **Clock & Candidate**: Core `50 MHz` (`20 ns`), JTAG TCK `1 MHz`, utilization `0.70`, hold target `0.15 ns`.
- **Precision & Size**: Native **`2000 DBU/µm`** preserved end-to-end.
  - Unsealed active die: `1920.24 × 1920.24 µm` (`3.687 mm²`, `<= 1950 µm` die limit).
  - Sealed `postDummy.gds` outer frame: `[-15.38, -15.38]–[1935.62, 1935.62] µm` = **`1951.0 × 1951.0 µm`** (`<= 1980 µm` sealed limit).
- **Zero-PDK Policy**: All GDS, CDL, DEF, LEF, SPEF, SDF, VCD, SVDB, and raw tool logs remain strictly on the authorized EDA environment.

## 1. 4-Corner 50 MHz MMMC STA & ±RC Sensitivity

All four PVT corners close `50.00 MHz` (`20.0 ns`) with **zero setup and zero hold violations**, zero RTL edits, and all 8 SRAM interface views + 452 CDC paths positive. Because the PDK supplies only a typical captable (`rc_note: "Typical captable only, temperature scaled; +-15%/+-20% RC scaling sensitivity verified"`), explicit ±15%/±20% RC scaling perturbations were evaluated on worst-case setup (`ss125`) and worst-case hold (`ffm40`).

| Corner | Clock | Setup WNS (`ns`) | Hold WNS (`ns`) | Fmax (`MHz`) | Setup / Hold Violations |
|---|---:|---:|---:|---:|---:|
| `ss125` (SS, 125 °C) | `20.0 ns` (`50 MHz`) | **`+0.492`** | **`+0.568`** | **`51.26`** | `0 / 0` |
| `tt25` (TT, 25 °C) | `20.0 ns` (`50 MHz`) | **`+7.083`** | **`+0.281`** | **`77.42`** | `0 / 0` |
| `ff0` (FF, 0 °C) | `20.0 ns` (`50 MHz`) | **`+10.824`** | **`+0.163`** | **`108.98`** | `0 / 0` |
| `ffm40` (FF, -40 °C) | `20.0 ns` (`50 MHz`) | **`+11.263`** | **`+0.144`** | **`114.46`** | `0 / 0` |

- **±RC Sensitivity** ([rc_sensitivity.tsv](rc_sensitivity.tsv)):
  - `ffm40` `-15% RC` Hold **`+0.125 ns`** (`0` viol); `-20% RC` Hold **`+0.121 ns`** (`0` viol).
  - `ss125` `+15% RC` Setup `-0.341 ns` (`49.16 MHz`); `+20% RC` Setup `-0.750 ns` (`48.19 MHz`).
- **Functional & Equivalence Signoff**: Post-layout **SS 125°C MAX SDF** gate simulation **PASS** (`41` JTAG commands, `0` unexpected warnings, `5` accepted time-0 input pad conflicts); Formality **LEC `verified`**; PrimeTime TT STA Setup **`+8.464 ns`** / Hold **`+0.297 ns`**; Innovus internal checks **`11/11` PASS**.

## 2. Windowed 100%-Annotated VCD Power (`TT 25 °C`, `50 MHz`)

- Matched `0.1`-activity vectorless TT25 reference: **`61.42 mW`** (`44.41` internal + `16.98` switching + `0.037` leakage).
- 5 workloads (`cnn_f1` `31.67 mW` / `0.3269 µJ`, `cnn_f0` `26.89 mW` / `0.7433 µJ`, `ecg` `44.48 mW` / `11.786 µJ`, `har` `49.09 mW` / `15.079 µJ`, `mnist_l` `52.91 mW` / `31.943 µJ`), all `100.0%` VCD toggle annotated.
- Fused vs. unfused CNN energy reduction: **`56.02%`** (`0.3269` vs `0.7433 µJ`).

## 3. Unified Physical Signoff on `postDummy.gds` (`2000 DBU/µm`)

- **DRC** (`calibre.drc`, `523` rules unmasked): **PASS (`0` blocking)**, `CO.R.4: 1 -> 0`, `M6.A.1 = 0`, `DOD.R.1 = 0`, `SR.* = 0`.
- **nmLVS** (`calibre.lvs`, full un-blackboxed SRAMs, `12/12` ports): **`CORRECT`** at `3.48%` (`45` IO `R(PR)` max `3.47%` at `0%`).
- **Antenna** (`62` rules): **`0 Hier. / 0 Exp. PASS`**.
- **WireBond** (`64` rules, `35` `PAD60LU_OBV`): **`0 Hier. / 0 Exp. PASS`**.
- **ERC** (`12` rules): **`7 Hier. / 977 Exp.`** (`0` design/core/SRAM defects; `formal_tapeout_signoff: false`).
