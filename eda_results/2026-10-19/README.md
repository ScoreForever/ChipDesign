# ChipDesign EDA snapshot — 2026-10-19

## Result classification

| Result | Status | What is proven |
|---|---|---|
| `smaller_cpu` / PicoRV32, 2800 µm core | **formal course-level result** | setup/hold clean at TT, Innovus DRC clean, final antenna clean, core VDD/VSS connectivity clean, maximum-delay SDF simulation PASS |
| `no_cpu`, 2800 µm core | **formal course-level result** | setup/hold clean at TT, Innovus DRC/antenna clean, core VDD/VSS connectivity clean, maximum-delay SDF simulation PASS |
| `no_cpu`, 2100 µm core | exploratory | route/export completed; DRC 0 and timing clean, but antenna 5 and post-layout simulation not rerun |
| `no_cpu`, 1700 µm core | diagnostic only | 2 mm die pad ring fits, but placement/routing cannot close because the core is too dense |

None of these results is foundry signoff.  Calibre DRC/LVS/PEX, MMMC STA,
IR/EM, power, DFT and final tapeout checks remain outside this snapshot.

## Entry points

- Full numeric summaries:
  - [`POSTLAYOUT_METRICS_smaller_cpu_PicoRV32.md`](../../POSTLAYOUT_METRICS_smaller_cpu_PicoRV32.md)
  - [`POSTLAYOUT_METRICS_no_cpu.md`](../../POSTLAYOUT_METRICS_no_cpu.md)
- Formal raw evidence:
  - [`formal/smaller_cpu`](formal/smaller_cpu/)
  - [`formal/no_cpu`](formal/no_cpu/)
- Die autosize analysis:
  - [`AUTOSIZE_DIAGNOSIS.md`](../../backend_experiments/no_cpu_autosize_20261018/AUTOSIZE_DIAGNOSIS.md)
  - [`parameterized Innovus scripts`](../../backend_experiments/no_cpu_autosize_20261018/scripts/)
  - [`raw experiment logs`](experiments/no_cpu_autosize/logs/)
- Baseline reports:
  - [`ASIC_POSTLAYOUT_REPORT.md`](../../docs/ASIC_POSTLAYOUT_REPORT.md)
  - [`ASIC_SIGNOFF_STATUS.md`](../../docs/ASIC_SIGNOFF_STATUS.md)
  - [`backend report PDF`](../../output/pdf/ChipDesign_backend_report_2026-10-17.pdf)

For `smaller_cpu`, `antenna_pre_repair.rpt` records the original single SRAM
data-net violation.  `antenna_final.rpt`, `drc_final.rpt` and
`regular_connectivity_final.rpt` are the authoritative post-repair checks.

## Repository policy

The public snapshot includes reports, logs, constraints, testbenches and
reproducible Tcl.  It excludes Innovus databases, Xcelium work libraries, DEF,
GDS, SPEF, SDF and gate-level netlists.  These products are large, generated,
and some contain foundry or SRAM physical IP.  See
[`ARTIFACT_MANIFEST.md`](ARTIFACT_MANIFEST.md) for identity hashes.
