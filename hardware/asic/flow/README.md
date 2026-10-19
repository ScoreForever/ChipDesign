# ASIC implementation flow

This directory contains the TSMC 180 nm course-server implementation flow used
for the 2026-10-16 ChipDesign post-layout run.

- `config/`: project and technology configuration.
- `pnr/scripts/init_pnr.tcl`: imports the mapped Genus handoff.
- `pnr/scripts/lab4_stage01_floorplan_io.tcl`: floorplan, IO ring and SRAM.
- `pnr/scripts/lab4_stage02_power_place.tcl`: power plan and placement.
- `pnr/scripts/lab4_stage03_cts_route_export.tcl`: CTS, routing, checks and
  DEF/GDS/netlist/SPEF/SDF export.
- `sim/run_postlayout_xcelium.sh`: stages the exported netlist/SDF and runs the
  integrated firmware regression with maximum-delay backannotation.

The corrected run is reproducible and passes the course-level Innovus DRC,
antenna, core-power connectivity, setup/hold, and maximum-delay SDF regression.
It is still not tapeout-ready because foundry Calibre DRC/LVS/PEX, multi-corner
signoff, IO-rail GDS verification, IR/EM/ESD, and DFT closure remain.  Results
and limitations are documented in
[`docs/ASIC_POSTLAYOUT_REPORT.md`](../../../docs/ASIC_POSTLAYOUT_REPORT.md).

Set `CHIPDESIGN_PROJECT_ROOT` before launching the flow when the synthesis/P&R
workspace is staged outside this Git checkout.  Foundry PDK, IO library and
SRAM views are intentionally not included in this public repository.

The optimized `smaller_cpu` and `no_cpu` results, raw lightweight audit reports
and SDF-simulation evidence are indexed under
[`eda_results/2026-10-19`](../../../eda_results/2026-10-19/README.md).
