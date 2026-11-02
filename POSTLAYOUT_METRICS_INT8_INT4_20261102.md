# No-CPU INT8 / INT4 post-layout results (2026-11-02)

Both frozen versions passed mapped synthesis, full-chip PnR, the existing nine Innovus physical gates, and elaboration-annotated maximum-delay SDF/JTAG simulation. This is a **course-level implementation result**, not independent signoff.

- [Chinese illustrated PDF report](docs/reports/int8_int4_20261102/NPU_INT8_INT4_后仿报告_20261102.pdf)
- [Machine-readable comparison](docs/validation/int8_int4_postlayout_20261102/comparison.json)
- [Raw non-private evidence archive](docs/validation/int8_int4_postlayout_20261102/final_evidence.tar.gz)
- [PDF generation source](docs/reports/int8_int4_20261102/build_report.py)

## Frozen sources and scope

| Item | INT8 | INT4 |
|---|---|---|
| Branch tested | `exp/no_cpu` | `no_cpu_INT4` |
| Commit | `fea96a2cc272875cfd860922edf5bc29405547aa` | `02185d8cb04542181facf264deb93b8eee2fc4e4` plus two fixes below |
| Model ABI | 2.1 | 3.0 |
| Configuration | 4x8; model and activation each one 8 KiB SRAM | Same physical SRAMs; packed INT4 |
| Tools | Genus 19.17-s144_1 / Innovus 21.16-s078_1 / Xcelium 21.09-s013 | Same |
| Corner / clocks | TT 1.8 V 25 C; core 50 MHz, JTAG 1 MHz | Same |

**Source currency:** `exp/no_cpu` advanced to `175484e` before publication, adding scoped ASIC mailbox CDC constraints and acceptance gates. That subsequent change was **not run here**. These old nine-gate results do not qualify the new scoped-CDC flow; no scoped payload timing or CDC protocol/metastability qualification is claimed.

## Results

| Metric | INT8 | INT4 |
|---|---:|---:|
| Synthesis standard-cell area (mm2) | 1.315184 | 1.112578 |
| SRAM macro area (mm2) / count | 0.802887 / 2 | 0.802887 / 2 |
| Synthesis total (mm2) | 2.118071 | 1.915465 |
| Synthesis total instance count | 53,358 | 48,149 |
| Post-route active standard-cell area (mm2) | 1.335700 | 1.130344 |
| Post-route active standard-cell instances | 55,340 | 49,730 |
| Die side including PAD ring (um) | 1940.40 | 1920.24 |
| Die / core area (mm2) | 3.765152 / 2.690387 | 3.687322 / 2.624659 |
| Setup / hold WNS (ns) | +0.457 / +0.020 | +0.612 / +0.020 |
| Setup / hold TNS (ns) | 0 / 0 | 0 / 0 |
| Innovus DRC / process antenna | 0 / 0 | 0 / 0 |
| All nine original physical gates | PASS | PASS |
| Regular DEF route centerline length (m) | 3.725215 | 3.160417 |
| Estimated global overflow H / V | 0.36% / 0.20% | 0.21% / 0.13% |
| SDF / physical JTAG | PASS / 70 commands | PASS / 64 commands |

INT4 mapped standard-cell area is 15.41% smaller; total synthesis area is 9.57% smaller and die area 2.07% smaller. Macro area is unchanged. These are one controlled `u70 west_stack + congestion` trial per version, not an optimization search or an independent repeat. No new workload performance or classification-accuracy claim is made.

The nine gates are antenna, signal connectivity, DRC, DRV, PAD geometry, PG connectivity, PG DRC, placement legality, and ports. Native IO power-rail information is handled by the existing bounded checker. Placement utilization, global routing usage/overflow, and detailed-route DRC have different denominators and meanings.

## INT4 corrections and failed attempts

1. `hardware/npu/rtl/npu_activation_store.sv`: move `wire [31:0] sram_q;` immediately before `sram_wdata` so Xcelium accepts its declaration before first use. No expression or hardware logic changes.
2. `hardware/asic/optimize/tb_fullchip.sv`: reset recovery expected ABI `32'h20001` changed to `32'h30000`, matching current hardware and trace. This changes only the testbench.

[Declaration patch record](docs/validation/int8_int4_postlayout_20261102/int4_compatibility_patch.json) and [ABI testbench patch record](docs/validation/int8_int4_postlayout_20261102/int4_abi3_testbench_patch.json) retain before/after SHA-256. The corresponding unified diff is also archived. Original failed `rtl/`, `int4_run/rtl2/`, original source archives and first exit receipts remain on the server. Final INT4 results are from fresh `int4_run_abi3/`.

## SDF acceptance and retained diagnostics

- SDF annotated at elaboration using `xrun -sdf_cmd_file`, maximum delay; annotation log exists and final result is `passed=true` for both.
- Representative nonzero physical-JTAG trace: load model/input, run two inferences, compare outputs, check IRQ and protocol/physical reset recovery. This is **not** all 74/82 RTL cases rerun at gate level.
- `NTC`, `RECREM`, negative timing checks and complete annotation logs are retained. Only six explicitly enumerated asynchronous CDC capture flops have timing checks disabled, as in the previous validated flow.
- SRAM CLK-to-Q edge qualifiers are rewritten to match the model; delay values remain unchanged. Model-declared `ifnone` warnings: 65,850 INT8 / 41,736 INT4 log records. Accepted SRAM negedge PERIOD records: six each. Accepted time-zero input-PAD conflicts: five each. These counts are log records, not independent failing arcs. Unexpected annotation diagnostics and timing violations: zero.
- Innovus retains two IMPSP-9099 diagnostics each: incomplete scan definition for 16.54% INT8 / 15.62% INT4 flops. DFT is not completed. These diagnostics are explicitly recorded, not erased by a zero process exit.

## Exports, server provenance and reproduction

All eight outputs exist and are nonempty: `simulation.v`, `physical.v`, `mapped.sdc`, `routed.def`, `routed.sdf.gz`, `routed.spef.gz`, `chip.lef`, `chip.gds.gz`. Output hashes are in each candidate's `artifact_sha256.json`.

Server base: `/home/s2400012724/chip-fullstack-2026/postlayout_latest_20261031`.

- INT8: `int8_source/`, `int8_run/candidates/course/`.
- INT4: `int4_source/`, `int4_run_abi3/candidates/course/`.
- Actual screenshots: `report_shots_int8/`, `report_shots_int4/`, rendered from **copies** of those final databases on 2026-11-02. The acceptance databases were not modified.

For reproduction, use a separate frozen source checkout at the exact commit, apply only the two documented INT4 corrections, source `/opt/eda/env.sh` and `/lec/eda.env`, and use a **new output directory**:

```bash
# First run the project's physical-JTAG RTL test against both real SRAM models.
# Timing checks are disabled only for this zero-delay RTL pretest.
python3 hardware/asic/optimize/flow.py trial \
  --output /absolute/new/run --name course \
  --utilization 0.70 --layout west_stack --congestion
python3 hardware/asic/optimize/postlayout.py \
  --candidate /absolute/new/run/candidates/course
```

The evidence archive retains synthesis area/timing/check reports, input hashes, final physical results and checks, leaf coordinates and disjoint name-based area groups (including explicit flattened residual), SDF classification and hashes, and both patch records. It excludes PDK/model files and complete design netlists/databases. Full logs and implementation outputs remain on the server. PDF regeneration requires Python `reportlab`, Windows `C:/Windows/Fonts/simhei.ttf`, and the included assets.

**Not executed:** independent Calibre DRC/LVS/PEX, PrimeTime, Formality, multi-corner STA, IR/EM, scan/ATPG, package/ESD or foundry tapeout signoff. Previous signoff results cannot be transferred to these snapshots.
