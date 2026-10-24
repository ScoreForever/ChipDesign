# ASIC implementation flow

This directory contains the course-server TSMC 180 nm Genus, Innovus, and
Xcelium flow used for ASIC synthesis, place-and-route, and post-layout
simulation.

## Flow stages

1. Configure the PDK, standard-cell libraries, SRAM macro views, and project
   root in `config/`.
2. Synthesize `my_soc_top` with the Genus script in
   `hardware/soc/synth/scripts/synth_genus.tcl`. The mapped ASIC build selects
   the course SRAM macro with `USE_RA1SHD_MAINMEM`.
3. In a fresh Innovus session, source `pnr/scripts/init_pnr.tcl`, then run the
   floorplan/IO, power/placement, and CTS/route/export stages in order.
4. Run `sim/run_postlayout_xcelium.sh` with the exported netlist and SDF to
   perform the integrated gate-level regression.

## Environment

The flow requires the course TSMC 180 nm PDK, IO library, SRAM timing/physical
views, and licensed Genus, Innovus, and Xcelium installations. These external
files are not included in the repository. Set `CHIPDESIGN_PROJECT_ROOT` when
the source checkout is staged outside the default relative location; the
post-layout simulation runner also requires `CHIPDESIGN_ROOT` and
`CHIPDESIGN_PNR_ROOT`.

The scripts provide a reproducible course flow, not foundry signoff by
themselves. Review the target RTL revision, constraints, tool configuration,
and resulting DRC/LVS, multi-corner STA, IR/EM, DFT, and post-layout simulation
evidence before making a tapeout decision. A main-aligned historical baseline
report is available on the
[EDA results branch](https://github.com/ScoreForever/ChipDesign/blob/eda-results-main-align-20261021/POSTLAYOUT_METRICS_BASELINE_MAIN_ALIGN.md).
