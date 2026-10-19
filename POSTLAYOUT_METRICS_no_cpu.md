# ChipDesign Post-Layout Metrics — `exp/no_cpu`

## Run identification

| Field | Value |
|---|---:|
| RTL Git commit | `48958c0ec5c176a886b5065747bbe170ae588f4c` |
| Branch | `exp/no_cpu` |
| Top module | `my_soc_top` |
| Architecture | CPU-less: JTAG transport + hardware controller + fused NPU |
| NPU implementation | `tinycnn8_npu_fused` (FUSED_CORE=1) |
| Process | TSMC 180 nm, 1P6M, core 1.8 V |
| STA corner / view | TT / 1.8 V / 25 °C, `func_tt_io` |
| RC corner | `rc_tt_1p80v_25c`, post-route, effort low, SI off |
| Genus / Innovus / Xcelium | 19.17-s144_1 / 21.16-s078_1 / 21.09-s013 |

## Synthesis

| Metric | Value |
|---|---:|
| Target clock period / frequency | 20.000 ns / 50 MHz |
| `core_clk` = 20 ns, `jtag_clk` = 1000 ns (1 MHz, active host interface) | async groups |
| Input/output delay | 2.000 ns |
| Mapped standard-cell count | 87,605 |
| Mapped standard-cell area | 2,355,548 µm² (2.36 mm²) |
| Synthesis WNS | +1.628 ns |
| Unresolved references / empty modules | 0 / 0 |
| SRAM hard macros | 0 (no shared main SRAM) |

### Synthesis hierarchy area

| Hierarchy | Cells | Area (µm²) | Area (mm²) | Share |
|---|---:|---:|---:|---:|
| `my_soc_top` | 87,605 | 2,355,548 | 2.355548 | 100.00% |
| `i_control` (controller + NPU) | 87,116 | 2,337,396 | 2.337396 | 99.23% |
| `i_control/i_npu` (`tinycnn8_npu_fused`) | 83,173 | 2,241,124 | 2.241124 | 95.14% |
| `i_control/i_controller` | 3,942 | 96,255 | 0.096255 | 4.09% |
| `i_host` (`npu_jtag_transport`) | 489 | 18,152 | 0.018152 | 0.77% |

## Floorplan and geometry

| Metric | Value |
|---|---:|
| Core width × height | 2,800.000 µm × 2,800.000 µm |
| Core area | 7.840000 mm² |
| Die width × height | 3,100.160 µm × 3,100.160 µm |
| Die area | 9.610992 mm² |
| Core-to-die margin | 150.080 µm |
| SRAM macro count | 0 |
| Post-route density excluding fillers | 32.468% |
| Post-route density including fillers | 100.000% |

## Post-route area (`report_area`)

| Metric | Count / area | Area (mm²) | Share |
|---|---:|---:|---:|
| Instances in `report_area` | 94,649 | — | — |
| Total `report_area` | 2,686,591 µm² | 2.686591 | 100.000% |
| Buffer | 67,693 µm² | 0.067693 | 2.52% |
| Inverter | 22,000 µm² | 0.022000 | 0.82% |
| Combinational | 1,037,725 µm² | 1.037725 | 38.63% |
| Flop | 1,559,172 µm² | 1.559172 | 58.03% |
| Clock gate | 57,205 µm² | 0.057205 | 2.13% |
| Macro | 0.000 µm² | 0.000000 | 0.00% |
| Synthesis→post-route area | +331,042 µm² vs synthesis (.lib vs LEF area) | 0.331042 | — |

## Clock tree

| Clock | Period | Frequency |
|---|---:|---:|
| `core_clk` | 20 ns | 50 MHz |
| `jtag_clk` | 1000 ns | 1 MHz |

## Post-route STA

| Mode | WNS | TNS | Violating paths | All paths | Reg-to-reg |
|---|---:|---:|---:|---:|---:|
| Setup | +1.047 ns | 0.000 ns | 0 | 40,066 | 38,704 |
| Hold | +0.020 ns | 0.000 ns | 0 | 40,066 | 38,704 |

### Design-rule timing constraints

| Check | Violating nets/terms | Worst violation |
|---|---:|---:|
| Maximum capacitance | 9 / 9 | −0.053 |
| Maximum transition | 13 / 55 | −0.342 |
| Maximum fanout | 0 / 0 | 0 |
| Maximum net length | 0 / 0 | 0 |

> Note: the 9 max-cap and 13 max-tran are minor transition/capacitance DRVs
> (not Innovus `verify_drc` geometry violations). Setup/hold are clean.

## Innovus physical checks

| Check | Result |
|---|---:|
| `verify_drc` | 0 violations |
| `verifyProcessAntenna` | 0 violations |
| Regular connectivity | 2 problems (VDDPST/VSSPST pad ring, no routing — expected) |
| Core `VDD/VSS` special connectivity | 0 problems |

## Post-layout SDF simulation

| Metric | Value |
|---|---:|
| Netlist | `my_soc_top_hier_postpnr.v` |
| SDF | `my_soc_top_tt_io.sdf.gz` |
| Delay selection | `MAXIMUM` |
| Testbench | `tb_nocpu_postlayout` (JTAG-only) |
| Completion | PASS |
| Result check | 4 logits bit-exact vs golden |
| Simulation time | 2,399,635 ns |
| Time-zero PAD bus conflicts | 5 (expected, non-fatal) |

## Exported implementation artifacts

| Artifact | Size | Status |
|---|---:|---:|
| `my_soc_top.def` | 172,654,493 B | generated |
| `my_soc_top.gds.gz` | 29,134,132 B | generated |
| `my_soc_top.lef` | 2,588 B | generated |
| `my_soc_top_hier_postpnr.v` | 13,384,937 B | generated |
| `my_soc_top_flat_lvs.v` | 28,186,102 B | generated |
| `my_soc_top_tt_io.sdf.gz` | 17,742,931 B | generated |
| `my_soc_top_tt_io.spef.gz` | 44,207,459 B | generated |
| `my_soc_top.sdc` | 1,681 B | generated |

## RTL optimization comparison (vs `main` baseline)

| Metric | baseline (CV32E40P) | no_cpu | Δ |
|---|---:|---:|---:|
| Synthesis cells | 94,601 | 87,605 | −7.4% |
| Synthesis cell area | 3.382 mm² | 2.356 mm² | **−30.3%** |
| SRAM macros | 1 | 0 | −1 (saves 0.401 mm²) |
| Post-route report_area | 3.669 mm² | 2.687 mm² | **−26.8%** |
| Post-route density (same 2800 µm core) | 42.98% | 32.47% | — |

**Takeaway**: removing the CPU + AXI interconnect + debug + shared main SRAM cuts
standard-cell area by ~30% and eliminates the SRAM macro entirely. The 2800 µm
core is now oversized (32% density), so the die can be shrunk substantially to
approach the 2 mm × 2 mm course target.

## Remaining signoff (not completed)

Calibre DRC/LVS/PEX, SS/FF + voltage/temperature MMMC, SI/crosstalk, IR drop,
EM, power, formal equivalence, DFT/scan/ATPG — all out of scope for this run.
