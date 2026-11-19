# Week 3 Lab 5 ERC Deep-Dive & Transistor-Level Audit (`2026-11-19`)

## Executive Summary

Calibre nmLVS/ERC (`calibre.lvs`, `PRECISION 2000`, `RESOLUTION 2`, `LVS POWER NAME "VDD" "VDDPST"`, `LVS GROUND NAME "VSS" "VSSPST"`, `LVS REDUCE SPLIT GATES YES`, both `RA1SHD_2048x32M8` SRAM macros un-blackboxed) was executed on the unified `2000 DBU/µm` `postDummy.gds` (`my_soc_top`, outer sealed die `1951.0 × 1951.0 µm <= 1980 µm`, SHA-256 `17fbc02c48f4b756c91788df420b7db307d506e03787af7be200faad55cc0a89`).

- **Rules executed**: `12` (`mppg`, `mnpg_ldd`, `mppg_ldd`, `mnpg_hvs`, `mppg_hvs`, `floating`, `floating.HVNW_float`, and `ppvdd49` all reported **`0 (0)`**).
- **Non-zero findings**: **`7` hierarchical / `977` expanded** across 4 rules (`832 + 72 + 72 + 1 = 977`).
- **Design-level defect count**: **`0`**. Every single expanded marker is accounted for down to the exact transistor finger / diode subcell inside unmodified vendor TSMC `0.18 µm` IO library cells (`tpd018nv_270a`) or the mandatory Seal Ring exterior substrate frame.
- **No illegal workarounds**: No virtual shorts, fake ground labels across the Seal Ring assembly isolation moat (`SR.S.1`), or modifications to vendor SPICE/PDK cells were introduced.
- **Tapeout status**: `formal_tapeout_signoff: false`. Residual vendor-IO and exterior-substrate ERC items remain open for formal foundry / library-owner signoff waiver.

---

## Complete Rule-by-Rule Breakdown

| ERC Rule | Hier / Exp | Representative Marker Box (`µm`) | Owning Cell / Subcell | Exact Instance & Device Accounting | Root Cause & Disposition |
|---|---:|---|---|---|---|
| `mnpg` | `4 / 832` | `[83.525, 933.430]–[107.525, 933.610]`<br>`[83.525, 934.550]–[107.525, 934.730]`<br>`[2.240, 1417.125]–[26.240, 1417.525]`<br>`[2.240, 1418.275]–[26.240, 1418.675]` | `PVDD1CDG` (`B776_POSTVDD1DGZ4`)<br>`PVDD2CDG` & `PVDD2POC` (`B776_POSTVDD2CDG`) | `8 × 56` (`448`) + `(4 + 4) × 48` (`384`) = **`832`** big-FET NMOS fingers | Intentional RC-triggered active rail ESD clamps (`VDD↔VSS`, `VDDPST↔VSSPST`). Retain for IO-library waiver. |
| `floating.nxwell_float` | `1 / 72` | `[1.575, 213.670]–[18.995, 219.760]` | `PVSS1CDG` (`B776_PDIOVSS1CDG`) | `8` `PVSS1CDG` pads × `9` diode units = **`72`** N-well islands | Antiparallel `VSS↔VSSPST` ESD diode cathode N-well tied to `GROUND_NAME` (`VSS`/`VSSPST`). Retain for IO-library waiver. |
| `npvss49` | `1 / 72` | `[1.975, 213.870]–[18.595, 219.560]` | `PVSS1CDG` (`B776_PDIOVSS1CDG`) | `8` `PVSS1CDG` pads × `9` diode units = **`72`** N-tap guard rings | Same cathode N-tap inside `B776_PDIOVSS1CDG` tied to `GROUND_NAME` (`VSS`/`VSSPST`). Retain for IO-library waiver. |
| `floating.psub_float` | `1 / 1` | `[-15.385, -15.385]–[1935.625, 1935.625]` (outer perimeter outside octagonal cutout `[0.13, 0.13]–[1920.11, 1920.11]`) | Exterior die / Seal Ring substrate frame | `1` continuous exterior perimeter polygon outside IO ring & `5 µm` assembly isolation gap | Mandatory Seal Ring & IO outer isolation boundary (`SR.S.1` forbids bridging `ASS_REGION`). Retain for foundry signoff. |

---

## Detailed Circuit & Physical Evidence

### 1. `mnpg` (`4` Hierarchical / `832` Expanded) — Active ESD Power-Rail Clamps

Calibre rule `mnpg` flags any non-RF NMOS whose source and drain diffusions connect directly to both a `POWER_NAME` net (`VDD` or `VDDPST`) and a `GROUND_NAME` net (`VSS` or `VSSPST`).

Cross-checking the chip's 35 IO pad instances (`PVDD1CDG: 8`, `PVDD2CDG: 4`, `PVDD2POC: 4`, `PVSS1CDG: 8`, `PVSS2CDG: 4`, `PDDW0204CDG: 7`) against the vendor IO library schematic topology:

- **Core-rail ESD clamp (`PVDD1CDG`, `8` instances)**:
  - Each `PVDD1CDG` instantiates subcell `B776_POSTVDD1DGZ4`, which implements an RC-triggered active rail ESD clamp consisting of **`56` parallel big-FET NMOS fingers** spanning `VDD` to `VSS` with gate driven by an internal RC-delay inverter trigger stage.
  - Expanded count: `8 × 56 = 448` NMOS fingers (`2` hierarchical marker rows inside `B776_POSTVDD1DGZ4`, e.g., West pad `PVDD1CDG` at `Y ∈ [933.43, 934.73] µm`).
- **I/O-rail ESD clamp (`PVDD2CDG` `4` instances + `PVDD2POC` `4` instances)**:
  - Both cells instantiate subcell `B776_POSTVDD2CDG`, which implements a 3.3V RC-triggered active rail ESD clamp consisting of **`48` parallel big-FET NMOS fingers** spanning `VDDPST` to `VSSPST`.
  - Expanded count: `(4 + 4) × 48 = 384` NMOS fingers (`2` hierarchical marker rows inside `B776_POSTVDD2CDG`, e.g., West pad `PVDD2CDG` at `Y ∈ [1417.125, 1418.675] µm`).
- **Verification**: `448 + 384 = 832` — exact match to the single transistor finger. No DC short exists because the clamp gate is actively held low at `VSS`/`VSSPST` under steady-state supply power. `0` standard-cell, SRAM, or routing transistors are flagged.

### 2. `floating.nxwell_float` (`1 / 72`) & `npvss49` (`1 / 72`) — Ground-Rail Antiparallel ESD Diodes

Calibre flags:
- `floating.nxwell_float`: N-well regions lacking a power-net path or connected directly to a `GROUND_NAME` net (`VSS`/`VSSPST`).
- `npvss49`: N-tap guard rings connected directly to a `GROUND_NAME` net (`VSS`/`VSSPST`).

Both marker polygons overlap the exact same physical structure inside West `PVSS1CDG` (`X ∈ [1.575, 18.995] µm`, `Y ∈ [213.670, 219.760] µm`):

- Each `PVSS1CDG` (`8` instances around the die) instantiates `9` `B776_PDIOVSS1CDG` subcell positions (`8 × 9 = 72` total diode units).
- Functionally, each `PVSS1CDG` pad contains an anti-parallel pair of ESD clamp diodes between core ground `VSS` and I/O ground `VSSPST` (alongside a reverse-biased protection diode from `VSS` to `VDDPST`).
- Because the cathode N-well and its N-tap ring inside `B776_PDIOVSS1CDG` are intentionally tied to a `GROUND_NAME` bus (`VSS`/`VSSPST`) rather than `VDD`/`VDDPST`, Calibre triggers both `floating.nxwell_float` (`72`) and `npvss49` (`72`) on the identical 72 diode islands.
- **Verification**: `8 × 9 = 72` — exact match. All core and SRAM N-wells are tied to `VDD`/`VDDPST` with zero floating N-well errors.

### 3. `floating.psub_float` (`1 / 1`) — Exterior Seal Ring & IO Perimeter Substrate

- The sole `floating.psub_float` marker is a single 15-vertex hollow frame spanning the sealed outer perimeter `[-15.385, -15.385]–[1935.625, 1935.625] µm` around an octagonal inner cutout `[0.130, 0.130]–[1920.110, 1920.110] µm` (45° corner chamfers at `70.566 µm`).
- Pre-Seal-Ring `chip.gds` already produces the exact same `1 (1)` marker along `[-0.005, -0.005]–[1920.245, 1920.245] µm` because the unbroken IO perimeter and corner cells separate the outer die border from the internal `VSS`/`VSSPST`-strapped p-substrate; adding the `5 µm` Seal Ring assembly isolation band (`ASS_REGION`) and `10 µm` Seal Ring extends this exterior substrate frame cleanly to `[-15.385, 1935.625] µm`.
- **Zero** internal core, SRAM, or IO active substrate regions are floating.
- Drawing metal/contacts across `ASS_REGION` to tie the outer scribeline substrate to `VSS` would violate DRC rule `SR.S.1` and compromise die-saw crack-stop isolation; therefore, no synthetic tie was added.

---

## Zero-Regression Comparison (Pre-Lab-5 vs. Final `postDummy.gds`)

| Metric | Pre-Seal-Ring `chip.gds` | Final `postDummy.gds` | Delta |
|---|---:|---:|---:|
| `mnpg` | `4 / 832` | `4 / 832` | `0` |
| `floating.nxwell_float` | `1 / 72` | `1 / 72` | `0` |
| `npvss49` | `1 / 72` | `1 / 72` | `0` |
| `floating.psub_float` | `1 / 1` | `1 / 1` | `0` (outer bbox expanded to sealed frame) |
| All other 8 ERC rules | `0 / 0` | `0 / 0` | `0` |
| **Total ERC** | **`7 / 977`** | **`7 / 977`** | **`0`** |

This confirms that the native `2000 DBU/µm` Seal Ring, the `CO.R.4` PIMP abutment patch in `B776_POSTVDD2CDG`, and full-chip `Dummy_Metal` (`M1`–`M6`, `0.8 × 0.8 µm` normal M6) + `Dummy_OD_PO` (`DOD`/`DPO`) insertion introduced **zero** ERC regressions.

---

## Manual Signoff Checklist (Retained Open)

- [ ] IO library / foundry signoff review of `mnpg` (`4 / 832`, `PVDD1CDG` / `PVDD2CDG` / `PVDD2POC` big-FET ESD clamps) against `tpd018nv_270a` release notes.
- [ ] IO library / foundry signoff review of `floating.nxwell_float` (`1 / 72`) and `npvss49` (`1 / 72`, `PVSS1CDG` `B776_PDIOVSS1CDG` `VSS↔VSSPST` ESD diodes).
- [ ] Foundry / packaging confirmation of exterior `floating.psub_float` (`1 / 1`) outside the Seal Ring assembly isolation zone.
- [ ] Independent filled-geometry RC extraction (`postDummy.gds`) + full-chip IR/EM + DFT review prior to formal tapeout signoff (`formal_tapeout_signoff: false`).
