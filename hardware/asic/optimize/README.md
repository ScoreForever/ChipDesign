# No-CPU full-chip physical optimization

This is an independent ABI-2 course flow for `my_soc_top`. It preserves the
legacy six-PAD SoC scripts. The full-chip build adds all seven signal PADs,
28 supply PADs, four corners, 35 wire-bond openings, IO/core fillers, the
two SRAM macros (model and activation store) with their power rings, a core
PDN, CTS and METAL2–6 routing. The die, including the PAD ring, is capped at
2 mm x 2 mm (`MAX_DIE_UM`); candidates whose computed die exceeds it are
recorded as over budget and never start Innovus.

Run on the authorized course node, in a frozen source checkout:

```bash
source /opt/eda/env.sh
source /lec/eda.env
python3 hardware/asic/optimize/flow.py preflight --output /absolute/new/experiment
python3 hardware/asic/optimize/flow.py baseline --output /absolute/new/experiment
python3 hardware/asic/optimize/flow.py search --output /absolute/new/experiment
python3 hardware/asic/optimize/postlayout.py --candidate /absolute/new/experiment/candidates/u60
```

Use the Python 3.8 environment available after sourcing EDA; the system Python
may be too old. Never copy private PDK or SRAM views into this repository.
Paths are discovered from the course installation, and every input is hashed.
Each candidate runs a private, hashed copy of the Tcl driver and saves output
artifact hashes. Preflight proves file/tool availability, not a license checkout. Real tool
invocation and the completion markers establish execution.

`search` / `resume` run serially with four threads: 55–75% utilization with
the `west_stack` layout (stopping at the first density that fails after a
pass), the `south_row` and `diagonal` layouts at the best valid density, one
congestion-policy candidate, and an independent repeat (ten complete-flow
attempts maximum). `trial --utilization U --layout L` runs one point. Failed
existing candidates are preserved rather than silently retried. Start a new
output directory after changing sources or flow scripts. A changed synthesis
handoff is rejected. A process timeout kills its process group and saves a
failed status. An independent worker preserves the real tool exit code after
controller disconnection. `resume` waits for the existing verified worker,
without launching a duplicate; missing exit receipts fail closed. A controller
lock prevents concurrent controllers for the same output directory.

Each macro keeps R0 and a 26um halo; the two macros are separated by both
halos plus a 20um channel and kept clear of the core ring. Layouts:
`west_stack` (stacked on the west edge), `south_row` (side by side on the
south edge) and `diagonal` (south-west and north-east corners). Instances are
matched by name (`*model_store*`, `*activation*`).
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
TT libraries and the course captable. No new timing exceptions or frequency
reduction are used for acceptance. Hold optimization targets +0.020ns, with the
optimization density cap raised from the course 0.75 to 0.9 (at 70% utilization
the default stopped hold buffering); physical eligibility still requires >=0.
After routing the flow adds floating M5/M6 metal fill (foundry minimum density
30%) and trims dangling special-wire stubs (`editTrim`) before the final checks
and extraction, so DRC, connectivity, SPEF and SDF all include them. Independent repeat
and baseline/final SDF checks are required before reporting an optimized result.
The physical JTAG test loads nonzero oracle data, compares two inference results,
checks IRQ and protocol/physical reset recovery (ABI 2.1) without hierarchy
backdoors. SDF is annotated at elaboration through `xrun -sdf_cmd_file`
(Xcelium ignores a run-time `$sdf_annotate` with a non-constant file name).
Timing checks are disabled on exactly six CDC capture flops, the req/ack
toggle-synchronizer first stages and both reset-synchronizer stages, which
sample asynchronous inputs by design; the list is matched in the netlist and
saved as `sdf/cdc_tcheck_disabled.json`. Every other check stays enabled and
any violation fails acceptance. The simulation uses `+define+NTC +define+RECREM
+neg_tchk` so the TSMC models take the SDF RECREM checks, and `-ignr_warnmax`
so the annotation log is complete. Accepted, and counted in `sdf/result.json`:
unannotatable edge-qualified IOPATHs that the cell model declares as `ifnone`
arcs (non-unate cells; used only for X inputs), the SRAM negedge CLK PERIOD
(the model checks posedge only) and one time-0 BUS CONFLICT on each input PAD
(the OEN tie cell has not propagated yet). The SRAM `(posedge CLK) Q[i]`
IOPATHs are rewritten without the edge to match the model's `(CLK => Q[i])`;
the count and source hash are in `sdf/sdf_rewrite.json`. Anything else fails.

## Independent signoff

`signoff.py --candidate <dir>` re-checks an eligible candidate's exported
files with engines other than Innovus, each in `<dir>/signoff/<check>/`:

- `drc`: Calibre DRC with the PDK deck (PRECISION/RESOLUTION rescaled to the
  2000 dbu GDS). Results are classified by rule and location. Accepted, and
  listed with counts: the deck's unconditional `DRM.R.1` reminder, and ESD/
  latch-up guideline (`…g`) or front-end-layer rules whose results all lie in
  the IO ring (vendor IO cells; this flow draws only metal and vias). Any
  other result blocks.
- `lvs`: Calibre LVS of the GDS against the physical netlist (v2lvs) plus the
  IO, standard-cell and SRAM SPICE. The POC rail gets a top-level label, and
  split gates are reduced (the IO pad NOR2 is fingered). Passes on CORRECT, or
  when the only discrepancies are IO-pad poly-resistor values within 5% (the IO
  SPICE 270a and GDS 280a differ by 3.47%); topology must match exactly.
- `sta`: PrimeTime with the routed netlist, SDC and Innovus SPEF. There is no
  Library Compiler on the node, so the SRAM `.lib` cannot be read: both SRAMs
  are black boxes and their interface timing is covered by Innovus only.
- `lec`: Formality, synthesized versus routed netlist (SRAMs black-boxed).

`signoff/summary.json` records every result. This is course-level signoff at
the TT corner: no multi-corner STA, IR/EM, seal ring or chip-level ESD review.

The summaries distinguish physical eligibility from gate/SDF acceptance. TT
and vectorless power are course implementation estimates, not foundry signoff,
workload power or proof of multi-corner closure. Archive GDS/DEF/SDF/SPEF, tool
logs, source/library hashes and database checkpoints on the server. Commit only
scripts, tests and bounded, non-private summaries.

Local framework tests:

```bash
python3 -m unittest discover -s hardware/asic/optimize -p 'test_*.py' -v
```

A completed existing course synthesis can be adopted without remapping:

```bash
python3 hardware/asic/optimize/flow.py adopt --output /absolute/new/experiment \
  --import-dir /absolute/completed/synthesis
```

Import requires the completion marker, clean error scan, zero exit code,
area report, exactly two SRAM macros in the netlist and a 1 ns SDC time unit
(`ns`, `1ns` or Genus' `1000ps`) with 20/1000ns clocks; it retains the original
driver and console. All candidates reuse the exact imported netlist bytes.
The common antenna policy permits at most two selected-net reroutes, saves
every attempt and rejects unrecognized reports or remaining violations.
IMPSP-9099 scan-chain diagnostics remain recorded separately when the tool
otherwise completes; they do not establish DFT completion. Other tool errors
remain fatal. CI tests the framework without private libraries or EDA licenses;
server implementation and SDF evidence are separate acceptance gates.

For a named controlled trial (within the same ten-attempt output budget):

```bash
python3 hardware/asic/optimize/flow.py trial --output /absolute/experiment \
  --name custom_u60 --utilization 0.60 --layout south_row \
  --halo 26 --congestion
```

Utilization is in (0,1), the layout is one of the three names above, and halo
is at least 26um.
A preserved name cannot be reused with changed parameters. The default search
keeps halo at 26um. Every point uses the same post-CTS/post-route setup/hold
repair policy. Import also requires `rtl_sources.json` from the original RTL
regression/synthesis preparation and verifies it against the current RTL and
constraint source, so historical netlists cannot be mislabeled as current.

`prects_cells.tsv/json` and `final_cells.tsv/json` retain actual instance boxes
and active-cell counts/areas, excluding fillers, taps, decaps, antenna and IO
cells. The reported macro count/area covers both SRAMs (model and activation);
wire-bond opening blocks are physical IO, not functional SRAMs. The difference
between final and pre-CTS active area is the net CTS/timing-repair change, not
an attribution of every added cell to the clock tree. All bond openings must
also stay within the actual snapped die; their intended overlays with electrical
PADs are excluded only from electrical-PAD overlap checks.
