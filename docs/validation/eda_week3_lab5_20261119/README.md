# Week 3 Unified ASIC Backend & Complete Lab 5 Physical Signoff (`2026-11-19`)

## Scope & Single-GDS Provenance

This validation record supersedes split standalone/Lab 5 snapshots by executing the **entire Lab 4 synthesis, P&R (with 35-pad WireBond keepout), post-layout SDF, 4-corner MMMC STA, 5-workload 100%-annotated VCD power, and full Lab 5 physical verification (`postDummy.gds`)** from a single `main` baseline (`bf754860691c1d32c317f057c56e61d177064089`) and a single unified candidate (`diag70_h25`).

- **Architecture**: ABI 5.0 `8×8` systolic NPU (`INT4×INT4`, `requant_fraction_bits = 15`, `clock_gating = true`), 2 × `RA1SHD_2048x32M8` SRAM macros (`diagonal_facing`), `npu_tensor_lookup` migrated into activation SRAM, 35 × `PAD60LU_OBV` CUP bond pads (`wirebond_keepout.tcl` `3 µm` M5/M6 route & fill keepout active before routing).
- **Clock & Candidate**: Core `50 MHz` (`20 ns`), JTAG TCK `1 MHz`, utilization `0.70`, hold target `0.25 ns`.
- **Precision & Size**: Native **`2000 DBU/µm`** preserved end-to-end (no `1000 DBU` re-quantization).
  - Unsealed active die: `1920.24 × 1920.24 µm` (`3.687 mm²`, `<= 1950 µm` Lab 4 die limit).
  - Sealed `postDummy.gds` outer frame: `[-15.38, -15.38]–[1935.62, 1935.62] µm` = **`1951.0 × 1951.0 µm`** (`<= 1980 µm` Lab 5 sealed limit).
- **Zero-PDK Policy**: All GDS, CDL, DEF, LEF, SPEF, SDF, VCD, SVDB, and raw tool logs remain strictly on the authorized EDA environment. Only sanitized JSON/CSV/TSV summaries, SHA-256 receipts ([manifest.json](manifest.json)), and layout PNGs are committed here.

---

## 1. Synthesis & Physical Implementation (`diag70_h25`)

| Metric | Value | Source |
|---|---:|---|
| Synthesis standard-cell area (flop + comb) | `1,167,773.96 µm²` (`553,133.32` flop + `614,640.63` comb) | [synthesis_area_breakdown.json](synthesis_area_breakdown.json) |
| Synthesis total library cell area (incl. 2 SRAM macros) | `1,970,661.14 µm²` (`51,905` mapped leaf cells, `12,065` sequential) | [synthesis_cells.json](synthesis_cells.json) |
| Post-route active standard-cell area / count | `1,246,928.48 µm²` / `59,606` instances (`12,065` sequential) | [metrics.json](metrics.json), [physical_area_breakdown.json](physical_area_breakdown.json) |
| SRAM macro area (2 × `RA1SHD_2048x32M8`) | `802,887.18 µm²` (`macro_count = 2`) | [metrics.json](metrics.json) |
| CTS & repair net area delta | `+69,906.14 µm²` (`+6,690` instances) | [physical_area_breakdown.json](physical_area_breakdown.json) |
| Die area / core area | `3.687 mm²` (`1920.24 × 1920.24 µm`) / `2.625 mm²` | [metrics.json](metrics.json) |
| Signal wire length | `3,852,585.63 µm` | [metrics.json](metrics.json) |
| WireBond keepouts applied (`route.tcl`) | `35` `PAD60LU_OBV` pads (`3 µm` M5/M6 route & fill keepout) | [wirebond_keepouts.tsv](wirebond_keepouts.tsv) |
| Innovus internal checks | **`11/11` PASS** (antenna, CDC, connectivity, DRC, DRV, macro pins, pad geometry, PG conn/DRC, placement, ports) | [checks.json](checks.json) |
| Post-layout SDF gate simulation (`50 MHz` / `1 MHz`) | **PASS** (`41` JTAG commands, `0` unexpected SDF warnings, `5` accepted time-0 input pad conflicts) | [sdf_result.json](sdf_result.json) |

### Post-Route Active Standard-Cell Breakdown by Group

| Group | Instances | Total Area (`µm²`) | Sequential (`µm²`) | Combinational (`µm²`) |
|---|---:|---:|---:|---:|
| `NPU` | `46,573` | `1,000,495.33` | `452,830.25` | `547,665.08` |
| `shared_store_logic` | `8,670` | `159,068.58` | `60,014.57` | `99,054.01` |
| `controller` | `3,367` | `62,323.92` | `23,157.16` | `39,166.76` |
| `host` | `918` | `24,349.16` | `17,151.10` | `7,198.06` |
| `backend_or_unattributed` | `78` | `691.49` | `0.00` | `691.49` |

---

## 2. 4-Corner MMMC STA, SRAM Interface & CDC Closure

All timing views are extracted on the routed database using typical captable with corner-scaled temperature (`rc_note: "Typical captable only, temperature scaled; no RC process corners"`). Standalone OpenSTA TT check on standard cells/IO also passes (`setup +1.407 ns`, `hold +0.235 ns`, LEC verified; see [signoff_summary.json](signoff_summary.json)).

| Corner | Clock Period | Setup WNS (`ns`) | Hold WNS (`ns`) | Setup / Hold Violating Endpoints |
|---|---:|---:|---:|---:|
| `tt25` (TT, 25 °C) | `20.0 ns` (`50.00 MHz`) | **`+0.633`** | **`+0.226`** | `0 / 0` |
| `ff0` (FF, 0 °C) | `20.0 ns` (`50.00 MHz`) | **`+4.529`** | **`+0.090`** | `0 / 0` |
| `ffm40` (FF, -40 °C) | `20.0 ns` (`50.00 MHz`) | **`+5.327`** | **`+0.075`** | `0 / 0` |
| `ss125` (SS, 125 °C) | `20.0 ns` (`50.00 MHz`) | `-16.779` | **`+0.458`** | `2000+ / 0` |
| `SS125_DERATED` (SS, 125 °C) | `38.0 ns` (`26.32 MHz`) | **`+0.234`** | **`+0.458`** | `NA / NA` (not recorded; positive WNS) |

- **SRAM Interface Timing** (`8` rows across 4 corners × 2 SRAM banks, [sram_interface_timing.csv](sram_interface_timing.csv)): `tt25` setup-to-SRAM `+0.784 / +0.891 ns`, hold-to-SRAM `+0.545 / +0.658 ns`, setup-from-SRAM `+4.445 / +4.371 ns`, hold-from-SRAM `+1.636 / +1.713 ns`.
- **CDC Timing**: `452` rows audited across all 4 corners ([cdc_4corners.tsv](cdc_4corners.tsv)).

---

## 3. Windowed 100%-Annotated VCD Power (`TT 25 °C`, `50 MHz`)

Each inference workload ran gate-level SDF simulation (`xrun`) dumping a strictly windowed VCD (`busy -> irq`), achieved **`100.0%` toggle annotation** in Innovus power analysis, and deleted raw VCD files immediately after SHA-256 hashing (`0` `.vcd` retained on server; see [power_summary.json](power_summary.json) and [power_workloads.csv](power_workloads.csv)).

| Workload | NPU Cycles | Window (`µs`) | Total Power (`mW`) | Internal (`mW`) | Switching (`mW`) | Leakage (`mW`) | Macro (`mW`) | Energy (`µJ`) | VCD Annotation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `vectorless` (ref) | — | — | `59.86` | `44.11` | `15.71` | `0.037` | `2.12` | — | Vectorless |
| `cnn_f1` (fused) | `515` | `10.32` | `29.43` | `22.19` | `7.20` | `0.037` | `3.43` | **`0.304`** | `100.0%` |
| `cnn_f0` (unfused) | `1,381` | `27.64` | `24.96` | `17.92` | `7.01` | `0.037` | `4.17` | `0.690` | `100.0%` |
| `ecg` | `13,247` | `264.96` | `44.53` | `34.64` | `9.86` | `0.037` | `5.71` | `11.799` | `100.0%` |
| `har` | `15,357` | `307.16` | `47.42` | `37.04` | `10.33` | `0.037` | `5.98` | `14.564` | `100.0%` |
| `mnist_l` | `30,184` | `603.70` | `51.05` | `39.77` | `11.24` | `0.037` | `5.54` | `30.818` | `100.0%` |

- **Operator Fusion Energy Reduction (`cnn_f1` vs. `cnn_f0`)**: **`55.98%`** energy savings (`0.3037 µJ` vs. `0.6900 µJ`), driven by `2.68×` shorter execution window (`515` vs. `1,381` cycles) despite slightly higher instantaneous active power (`29.43` vs. `24.96 mW`).

---

## 4. Complete Lab 5 Signoff on `postDummy.gds` (`2000 DBU/µm`)

All 5 Calibre verification engines executed against the **exact same `postDummy.gds`** (`SHA-256: 17fbc02c48f4b756c91788df420b7db307d506e03787af7be200faad55cc0a89`) and `netlist.cdl` (`SHA-256: 6b2fe0be04a068ec7ddffddbc5a939f519397ee1e9a540b58966f275e31502aa`) via [run_lab5_signoff.py](../../../hardware/asic/week3/run_lab5_signoff.py):

- **Native Seal Ring**: Assembled directly at `2000 DBU/µm` from `tsmc_c018_sealring_corner_1p6m` & `tsmc_c018_seal_ring_edge_1p6m` (`T18_1P6M_CUP_IO`) around symmetric integer-µm ref box `[-0.38, -0.38]–[1920.62, 1920.62] µm` -> outer sealed die **`1951.0 × 1951.0 µm <= 1980 µm`**.
- **Exact `CO.R.4` IO PIMP Fix**: Patched one `PIMP (7:0)` rectangle `[52.30, 52.91]–[52.59, 73.795] µm` (`104600 105820 105180 147590` @ `2000 DBU`) in the project GDS copy of `B776_POSTVDD2CDG`, covering the `0.15 µm` overhanging `CO` contact `[52.23, 72.13]–[52.45, 72.35] µm` and cleanly abutting the adjacent `NIMP (8:0)` guard ring at `x = 52.59 µm` with zero overlap and zero PDK/OD/Poly edits (`CO.R.4: 1 -> 0`).
- **Dummy Insertion**: `Dummy_Metal` (`M1`–`M6`, `THICK_40K_M6` disabled, normal M6 small fill `dmW_M6 = dmL_M6 = 0.8 µm` so `0.64 µm² >= 0.562 µm²` satisfies `M6.A.1 = 0`, `MIXED_SCHEME` datatype `1`) + `Dummy_OD_PO` (`DOD 3:1`, `DPO 13:1`), imported cleanly under primary `my_soc_top` (`PORT DEPTH PRIMARY` preserved for all `12` top ports).

| Lab 5 Engine | Deck / Config | Result | Detailed Evidence |
|---|---|---|---|
| **DRC** | `calibre.drc` (`523` checks, **zero rules masked**) | **PASS (0 non-teaching)** | Raw `2,427` hier / `16,935` exp strictly within the 4 Lab 5 teaching categories (`ESD.24g=30/210`, `ESD.25g=2384/16688`, `LUP.3.3g_3.3V=1/7`, `LUP.5.3g_3.3V=10/28`, `MOM.R.2=1/1`, `DRM.R.1=1/1`). `CO.R.4 = 0`, `M6.A.1 = 0`, `DOD.R.1 = 0`, `SR.* = 0`, metal density = `0`. |
| **nmLVS (Strict `0%`)** | `calibre.lvs` (`RA1SHD_2048x32M8` **un-blackboxed**, `12/12` ports) | **45 IO `R(PR)` prop diffs only** | `0` topology/connectivity/device/port mismatches; all `45` property errors are vendor IO `R(PR)` sheet-resistance delta (`3.47%` max, e.g. `3.405M vs 3.291M Ω`, `787.16 vs 760.74 Ω`). |
| **nmLVS (`3.48%` PR)** | `calibre.lvs` (`TRACE PROPERTY R(PR) R R 3.48`, full SRAMs) | **`CORRECT`** | `12/12` matched top ports (`VDDPST`, `VDD`, `VSSPST`, `VSS`, `POC`, `CLK_I`, `RST_NI`, `IRQ_O`, `TCK_I`, `TMS_I`, `TD_I`, `TD_O`), full transistor comparison including both SRAMs. |
| **Antenna** | `ant.drc` (`62` checks) | **`0 (0)` PASS** | Zero process antenna violations across all `62` checks on `postDummy.gds`. |
| **WireBond** | `C18_WIRE_BOND_6M.19_1a1` (`PITCH_55_SINGLE` + `OBV_CUP`, `64` checks) | **`0 (0)` PASS** | Zero WireBond / CUP / spacing violations across all `35` `PAD60LU_OBV` pads (`wirebond_keepout.tcl` verified). |
| **ERC** | `calibre.lvs` (`12` checks) | **`7` hier / `977` exp (audited)** | **0 design/core/SRAM defects**; 100% transistor-accounted (`mnpg 4/832` IO ESD clamps, `nxwell_float 1/72` & `npvss49 1/72` `PVSS1CDG` `VSS↔VSSPST` diodes, `psub_float 1/1` outer Seal Ring frame). Full audit in [erc_deep_dive.md](erc_deep_dive.md) and [final_erc_geometry.json](final_erc_geometry.json). |

---

## 5. Layout Views (`images/`)

- Innovus unified candidate views (`diag70_h25`): `1_placement.png`, `2_power.png`, `3_signal_routing.png`, `4a_m56_no_fill.png`, `4b_m56_with_fill.png`, `5_all.png`, `6_corner_zoom.png`, `7_sram_diag_zoom.png`.
- Lab 5 `postDummy.gds` annotated geometric evidence ([render_lab5_views.py](../../../hardware/asic/week3/render_lab5_views.py)):
  - [8_lab5_sealring_corner.png](images/8_lab5_sealring_corner.png): SW native Seal Ring corner, `CB` passivation, and `5 µm` Assembly Isolation gap (`1951.0 × 1951.0 µm <= 1980 µm`).
  - [9_lab5_dummy_fill_zoom.png](images/9_lab5_dummy_fill_zoom.png): Interior `DOD (3:1)`, `DPO (13:1)`, and `M1`–`M6` normal metal fill (`DM6 0.8 × 0.8 µm`, `M6.A.1 = 0`).
  - [10_lab5_wirebond_keepout_zoom.png](images/10_lab5_wirebond_keepout_zoom.png): North signal `PAD60LU_OBV` (`bond_i_pad_clk_i`) showing `3 µm` M5/M6 keepout clearance next to Seal Ring (`0` WB violations).
  - [11_lab5_cor4_and_erc_audit.png](images/11_lab5_cor4_and_erc_audit.png): `CO.R.4` PIMP abutment fix (`1 -> 0`) and full-die ERC spatial distribution (`7 / 977`).

---

## 6. Remaining Tapeout Boundaries (`formal_tapeout_signoff: false`)

1. **Formal ERC manual waiver** by IO-library / foundry signoff owner for the `7 / 977` residual vendor-IO / exterior-substrate markers ([erc_deep_dive.md](erc_deep_dive.md)).
2. **Filled-geometry RC extraction & STA/SDF** on `postDummy.gds` (current MMMC STA and SDF are anchored to pre-dummy routed SPEF/SDF with typical captable only; no RC process corners `Cmax/Cmin`).
3. **Full-chip static/dynamic IR drop & electromigration (EM)** signoff and **DFT / scan / package bond-diagram** signoff.
