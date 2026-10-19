# ChipDesign Post-Layout Metrics — `exp/smaller_cpu_PicoRV32`

## Run identification

| Field | Value |
|---|---:|
| RTL Git commit | `f80a45bc11e28a9fa7e9511df68daf339966367e` |
| Branch | `exp/smaller_cpu_PicoRV32` |
| Top module | `my_soc_top` |
| Architecture | PicoRV32 RV32IMC + AXI + JTAG DMI/SBA + fused NPU |
| NPU implementation | `tinycnn8_npu_fused` (FUSED_CORE=1) |
| Process | TSMC 180 nm, 1P6M, core 1.8 V |
| STA corner / view | TT / 1.8 V / 25 °C, `func_tt_io` |
| RC corner | `rc_tt_1p80v_25c`, post-route, effort low, SI off |
| Genus / Innovus / Xcelium | 19.17-s144_1 / 21.16-s078_1 / 21.09-s013 |

## Synthesis

| Metric | Value |
|---|---:|
| Target clock period / frequency | 20.000 ns / 50 MHz |
| Input/output delay | 2.000 ns |
| Mapped standard-cell count | 93,031 |
| Mapped standard-cell area | 3,183,377 µm² (3.18 mm²) |
| SRAM hard-macro area | 401,444 µm² (0.40 mm²) |
| Synthesis WNS | +2.642 ns |
| Unresolved references / empty modules | 0 / 0 |
| SRAM hard macros | 1 (`RA1SHD_2048x32M8`, via ported `USE_RA1SHD_MAINMEM`) |

### Synthesis hierarchy area (selected)

| Hierarchy | Cells | Area (µm²) | Area (mm²) |
|---|---:|---:|---:|
| `my_soc_top` | 93,031 | 3,183,377 | 3.183377 |
| `i_cpu` (`picorv32_soc`) | 6,241 | 167,470 | 0.167470 |
| `i_cpu/core` (`picorv32`) | 6,139 | 163,551 | 0.163551 |

> For reference, the baseline CV32E40P "CPU core" was 11,337 cells /
> 0.257 mm², so PicoRV32 is ~35% smaller in both cells and area.

## Floorplan and geometry

| Metric | Value |
|---|---:|
| Core width × height | 2,800.000 µm × 2,800.000 µm |
| Core area | 7.840000 mm² |
| Die width × height | 3,100.160 µm × 3,100.160 µm |
| Die area | 9.610992 mm² |
| Core-to-die margin | 150.080 µm |
| SRAM macro | 1 × `RA1SHD_2048x32M8` |
| Post-route density excluding fillers | 39.364% |
| Post-route density including fillers | 100.000% |

## Post-route area (`report_area`)

| Metric | Count / area | Area (mm²) | Share |
|---|---:|---:|---:|
| Instances in `report_area` | 97,155 | — | — |
| Total `report_area` | 3,412,556 µm² | 3.412556 | 100.000% |
| Buffer | 55,405 µm² | 0.055405 | 1.62% |
| Inverter | 21,037 µm² | 0.021037 | 0.62% |
| Combinational | 1,009,679 µm² | 1.009679 | 29.59% |
| Flop | 1,938,179 µm² | 1.938179 | 56.80% |
| Clock gate | 66,666 µm² | 0.066666 | 1.95% |
| Macro (`RA1SHD`) | 388,256 µm² | 0.388256 | 11.38% |
| Synthesis→post-route std-cell area | 3,024,300 µm² (report_area − macro) | 3.024300 | — |

## Clock tree

| Clock | Period | Frequency |
|---|---:|---:|
| `core_clk` | 20 ns | 50 MHz |
| `jtag_clk` | 100 ns | 10 MHz (DMI/SBA; JTAG inactive for functional test) |

## Post-route STA

| Mode | WNS | TNS | Violating paths | All paths | Reg-to-reg |
|---|---:|---:|---:|---:|---:|
| Setup | +2.681 ns | 0.000 ns | 0 | 55,312 | 53,663 |
| Hold | +0.019 ns | 0.000 ns | 0 | 55,312 | 53,663 |

### Design-rule timing constraints

| Check | Violating nets/terms | Worst violation |
|---|---:|---:|
| Maximum capacitance | 0 / 0 | 0.000 |
| Maximum transition | 0 / 0 | 0.000 |
| Maximum fanout | 0 / 0 | 0 |
| Maximum net length | 0 / 0 | 0 |

## Innovus physical checks

| Check | Result |
|---|---:|
| `verify_drc` | 0 violations |
| `verifyProcessAntenna` | 0 violations (after `sram_wdata[0]` re-route repair) |
| Regular connectivity | 2 problems (VDDPST/VSSPST pad ring, no routing — expected) |
| Core `VDD/VSS` special connectivity | 0 problems |

> The initial post-route antenna run reported 1 violation on `sram_wdata[0]`
> at the SRAM `D[0]` pin (M3 ratio 409 vs 400). It was cleared by deleting and
> re-routing that net with antenna-fix enabled (jump to higher metal).

## Post-layout SDF simulation

| Metric | Value |
|---|---:|
| Netlist | `my_soc_top_hier_postpnr.v` |
| SDF | `my_soc_top_tt_io.sdf.gz` |
| Delay selection | `MAXIMUM` |
| Testbench | `chipdesign_soc_postlayout_tb` (firmware → SRAM macro `mem`) |
| Firmware | `chipdesign_npu_test.hex` |
| Completion | PASS |
| Completion cycles | 47,390 |
| Completion simulation time | 947,950 ns |
| NPU IRQ observed | PASS |

> Post-sim ran on the pre-repair exported netlist/SDF; the repair only re-routed
> one SRAM data net (logic unchanged), so the functional result is unaffected.

## Exported implementation artifacts

| Artifact | Size | Status |
|---|---:|---:|
| `my_soc_top.def` | 168,366,835 B | generated |
| `my_soc_top.gds.gz` | 25,646,801 B | generated |
| `my_soc_top.lef` | 2,524 B | generated |
| `my_soc_top_hier_postpnr.v` | 15,379,639 B | generated |
| `my_soc_top_flat_lvs.v` | 46,779,440 B | generated |
| `my_soc_top_tt_io.sdf.gz` | 18,063,016 B | generated |
| `my_soc_top_tt_io.spef.gz` | 36,046,979 B | generated |
| `my_soc_top.sdc` | 1,612 B | generated |

## RTL optimization comparison (vs `main` baseline)

| Metric | baseline (CV32E40P) | smaller_cpu (PicoRV32) | Δ |
|---|---:|---:|---:|
| CPU cells | 11,337 | 6,241 | −45.0% |
| CPU area | 0.257 mm² | 0.167 mm² | −34.9% |
| Synthesis cells | 94,601 | 93,031 | −1.7% |
| Synthesis cell area | 3.382 mm² | 3.183 mm² | −5.9% |
| SRAM macros | 1 | 1 | 0 |
| Post-route setup WNS | +3.625 ns | +2.681 ns | still met |
| Post-route density (same 2800 µm core) | 42.98% | 39.36% | — |

**Takeaway**: swapping CV32E40P → PicoRV32 cuts the CPU block ~35%, but the NPU
still dominates (~2.24 mm² of the ~3.18 mm²), so the whole-chip area drops only
~6%. To shrink the die materially, the NPU (behavioral weight/activation
memories mapped to flops) is the next target, not the CPU.

## Remaining signoff (not completed)

Calibre DRC/LVS/PEX, SS/FF + voltage/temperature MMMC, SI/crosstalk, IR drop,
EM, power, formal equivalence, DFT/scan/ATPG — all out of scope for this run.
