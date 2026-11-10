# Reproduction and provenance

The measured synthesis used base `8c16462` plus the exact files in `candidate_sources_sha256.json`. The r2 physical run used `candidate_r2_sources_sha256.json`; its native handoff retained the same synthesized RTL, library inputs and clock-gating profile. The `current_sources_sha256.json` records the local implementation after incorporating the tested ECO policy and hierarchical SDF capture-name support.

With licensed tools and matching PDK paths configured, a fresh run of the committed flow is:

```bash
python3 hardware/asic/optimize/flow.py synth --clock-gating --cols 8 --output /absolute/new/experiment
python3 hardware/asic/optimize/flow.py trial --clock-gating --cols 8 --name area129_icg_diag75 --utilization .75 --layout diagonal_facing --halo 26 --hold-target .12 --output /absolute/new/experiment
python3 hardware/asic/optimize/postlayout.py --candidate /absolute/new/experiment/candidates/area129_icg_diag75
```

The published evidence came from r2 followed by a checkpoint ECO, not a fresh run of the final combined driver. These scripts preserve that actual procedure: `start_area_r2.sh` imports the matching completed synthesis, `run_route_eco.py` restores and hashes the failed physical checkpoint and performs bounded DRC repair, and `run_area_sdf.py` invokes the preserved `postlayout_hierarchical.py`. Their server-specific directory names refer to retained immutable experiments. Set up separate new directories before replaying; never overwrite original evidence. The combined route driver now performs the same bounded DRC repair before final checks/exports; an independent full rerun remains pending.

`fullchip_icg/sdf/status.json` preserves the raw launch failure classification caused by five time-zero input-pad initialization conflicts. `result.json` is the final narrow-policy acceptance receipt and records those five accepted diagnostics; it does not hide them. Full tool logs and private/large GDS, netlists, SPEF and SDF remain on the server; archived manifests identify their hashes. The archive contains reports and project code, not PDK models.
