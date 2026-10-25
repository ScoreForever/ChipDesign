# No-CPU full-chip physical optimization

This is an independent ABI-2 course flow for `my_soc_top`. It preserves the
legacy six-PAD SoC scripts. The full-chip build adds all seven signal PADs,
28 supply PADs, four corners, 35 wire-bond openings, IO/core fillers, the
model SRAM and its power ring, a core PDN, CTS and METAL2–6 routing.

Run on the authorized course node, in a frozen source checkout:

```bash
source /opt/eda/env.sh
source /lec/eda.env
python3 hardware/asic/optimize/flow.py preflight --output /absolute/new/experiment
python3 hardware/asic/optimize/flow.py baseline --output /absolute/new/experiment
python3 hardware/asic/optimize/flow.py search --output /absolute/new/experiment
python3 hardware/asic/optimize/postlayout.py --candidate /absolute/new/experiment/candidates/u50
```

Use the Python 3.8 environment available after sourcing EDA; the system Python
may be too old. Never copy private PDK or SRAM views into this repository.
Paths are discovered from the course installation, and every input is hashed.
Preflight proves file/tool availability, not a license checkout. Real tool
invocation and the completion markers establish execution.

`search` / `resume` run serially with four threads: 50/55/60/65% utilization,
two macro positions at the best valid density, one congestion-policy candidate,
and an independent repeat (eight complete-flow attempts maximum). Failed
existing candidates are preserved rather than silently retried. Start a new
output directory after changing sources or flow scripts. A changed synthesis
handoff is rejected. A process timeout kills its process group and saves a
failed status; never resume while the saved PID is still running.

The macro keeps R0 and a 26um halo. Its fractional position refers to the
legal lower-left placement interval, reserving room for rings and channels.
Signal PADs are grouped north (clock/reset/IRQ) and south (JTAG); supply PADs
follow the course grouping on every side. PAD coordinates scale with core
geometry. Electrical PAD/corner/filler overlap and out-of-die boxes are checked
separately from the intentional wire-bond opening overlays.

## Acceptance

Eligibility requires complete raw reports, nonnegative setup/hold, zero TNS
and DRV, legal placement, valid ports/PAD geometry, clean regular/special DRC,
connectivity and antenna checks. Native abutting IO rails VDDPST/VSSPST are
separately checked; their specific regular-net no-routing information is not
silently generalized to other networks. Unknown/missing report formats fail
closed. Tool exit zero alone is insufficient (Genus can return zero on errors).

Candidates are ranked by actual die area, then setup slack and wire length.
Wire length is regular DEF route centerline length, including signal/clock
nets but excluding SPECIALNETS, vias and patch area. It is not HPWL. Local
cell density, estimated global congestion and final detailed-route DRC are
different measures and must not be conflated.

The default is 20ns core and 1000ns JTAG, 2ns IO budget, 0.2ns uncertainty,
TT libraries and the course captable. No new timing exceptions, frequency
reduction or disabled timing checks are used for acceptance. Hold optimization
targets +0.020ns; physical eligibility still requires >=0. Independent repeat
and baseline/final SDF checks are required before reporting an optimized result.
The physical JTAG test loads nonzero oracle data, compares two inference results,
checks IRQ and protocol/physical reset recovery without hierarchy backdoors.

The summaries distinguish physical eligibility from gate/SDF acceptance. TT
and vectorless power are course implementation estimates, not foundry signoff,
workload power or proof of multi-corner closure. Archive GDS/DEF/SDF/SPEF, tool
logs, source/library hashes and database checkpoints on the server. Commit only
scripts, tests and bounded, non-private summaries.

Local framework tests:

```bash
python3 -m unittest discover -s hardware/asic/optimize -p 'test_*.py' -v
```
