# EGo1 FPGA validation stage gates

Target: EGo1 V2.2 (`xc7a35tcsg324-1`) with external FT232H fabric JTAG at 1 MHz.
Each gate requires a human review of its evidence before the next stage starts.

Frequency requirement confirmed by the user: **50 MHz for the formal release**.
All 25 MHz evidence in this document is **diagnostic/test evidence only**. It does
not satisfy the formal release clock requirement. The earlier 50 MHz candidate
failed setup timing (WNS -0.549 ns); the corrected H16/reset source still needs a
fresh 50 MHz implementation, timing closure, and board verification.

## Stage map

| Stage | What it establishes | Evidence to inspect | Pass condition |
|---|---|---|---|
| 0. Source and board setup | Correct branch, device, pin map, tool version, and cable wiring | `git status`, board manual, pin XDC, cable continuity, Vivado target | Part and every external pin match the actual board and wiring; local changes are understood |
| 1. RTL simulation | Protocol and operator behavior before mapping | Regression log, test list, failures, coverage or scenario summary | Required tests pass; directed reset, JTAG, memory, and operator cases are covered |
| 2. Synthesis | RTL elaborates and maps within FPGA resources | Synthesis log, utilization, inferred RAM/DSP, timing and unconstrained checks | No errors; resource mapping is expected; no unexplained unconstrained paths or warnings |
| 3. CDC and reset audit | Cross-clock handshakes and reset release are intentional | `report_cdc`, clock interaction, synchronizer RTL, reset XDC | No unexplained critical CDCs; bundled-data holds are checked against the handshake RTL |
| 4. Optimization and DRC | Netlist and device rules are clean before placement | `opt_design` log, DRC and methodology reports | Zero DRC errors; warnings are individually classified |
| 5. Placement | Cells fit and the initial timing estimate is credible | Placement log, utilization, timing, DRC, clock routes | No placement errors; no negative setup/hold slack; clock resources and endpoints are expected |
| 6. Routing and timing | All nets route and final setup/hold constraints pass | Route status, final timing summary and worst paths, DRC, unconstrained paths | No unrouted nets or DRC errors; setup and hold pass; all real IOs are constrained |
| 7. Bitstream generation | The reviewed routed checkpoint can produce a configuration image | Bitgen log, DRC, file size/hash, part, source checkpoint identity | Bitgen succeeds and artifact identity matches the reviewed routed checkpoint |
| 8. FPGA programming and JTAG smoke test | The actual board configures and the fabric TAP responds | Programmer log, `DONE`/clock LEDs, IDCODE, IR/DR tests, cable and pin notes | Board configuration succeeds; expected IDCODE and deterministic TAP reads pass |
| 9. NPU functional run | The physical NPU executes a known workload correctly | Model/input hashes, host reference, command trace, output bytes, status/errors | Output matches an independent reference byte-for-byte, with repeatable reset and run behavior |

Passing a stage means only that stage's checks passed. A generated bitstream does not
establish that the FPGA was programmed or that NPU inference worked on the board.

## RTL, synthesis, and CDC evidence

Build directory: `build/fpga_ego1_20261024_h16_resetfix_25mhz_v3/`.
The candidate uses the verified EGo1 J5 pin 21 / FPGA H16 for TCK. Earlier E15
candidate images are invalid and must not be programmed.

Synthesis completed with zero errors and zero critical warnings. The summary reports
8,908 LUTs (42.83%), 4,493 registers (10.80%), 4 BRAM tiles (8%), 48 DSPs (53.33%),
and one MMCM. Pre-placement WNS is +18.592 ns, TNS is 0 ns, and WHS is +0.045 ns.
The report has no unconstrained internal endpoints, zero external data inputs missing
input delay, and zero outputs missing output delay. `rst_ni` is the one input without
an input delay because it is asynchronous reset; the two LED outputs are explicitly
false-pathed because they have no synchronous receiver.

The methodology report still has 11 warnings: one `LUTAR-1` because
`rst_ni & clk_locked` drives the system reset synchronizer's asynchronous clear,
six BRAM output-register timing advisories, and four wide-multiplier advisories.
The `LUTAR-1` is a reset-integrity review item: the combined reset may glitch, so
its behavior must be inspected and exercised during reset tests. It is not cleared
by the added TCK-local synchronizer. DSP pipeline advisories also remain in DRC.

The CDC report has no Critical CDC entries. Three reset crossings and two toggle
synchronizers are recognized as `ASYNC_REG`. It retains 109 `CDC-15` warnings:
72 request payload bits (`req_payload` into the system command registers) and 37
response mailbox bits (`rsp_mailbox_*` into the TCK reply registers). The source
registers hold each bundled payload while the toggle handshake is outstanding; the
request remains pending until the synchronized acknowledgement returns, and the
response mailbox remains unchanged until the TCK side observes that acknowledgement.
This is the intended multi-bit bundled-data protocol, but the warning is not waived
automatically: a reviewer must confirm these hold intervals and that no unrelated
fanout or early overwrite exists in `hardware/soc/rtl/control/npu_jtag_transport.sv`.

The reset audit found and corrected the top-level JTAG activity counter's direct use
of asynchronous `rst_ni`: its release is now synchronized in the TCK domain. The
system reset synchronizer and activity-counter reset synchronizer carry `ASYNC_REG`.
The external reset is excepted from data-path timing, while reset assertion/release
still requires directed simulation and board checks.

Evidence files: `summary.md`, `synth_timing_summary.rpt`, `synth_drc.rpt`,
`synth_methodology.rpt`, `cdc.rpt`, and `clock_interaction.rpt` in the build directory.
The user approved this RTL/synthesis/CDC gate. Subsequent implementation stages are
recorded below; bitstream generation and board programming remain pending.

RTL evidence is in `build/nocpu_rtl_regression_fpga_20261024/`: all 56 programmable
NPU program scenarios passed, including valid/invalid descriptors, lifecycle, JTAG
transport, stalls, and 20,000 quantization vectors. A focused `tb_fpga_reset.sv`
simulation also passed asynchronous reset assertion and two-clock synchronized
release for the system and TCK indicator domains. That focused test uses clock-wizard
and SoC stubs; it checks the changed top-level reset behavior, not NPU operation or
physical clock-lock quality.

## Optimization/DRC gate

The user's review approved the RTL/synthesis/CDC gate. `opt_design` has now run on
that same reset-fixed synthesis checkpoint. The optimized
timing report has WNS +18.761 ns, TNS 0 ns, WHS +0.045 ns, with zero unconstrained
internal endpoints and no missing input/output delays except the documented
asynchronous reset and LED false paths. Utilization is 8,868 LUTs, 4,493 registers,
4 BRAM tiles, and 48 DSPs.

Full DRC has zero errors and 75 warnings: 46 DSP input-pipeline, 8 DSP PREG output,
and 21 DSP MREG output advisories. The targeted EGo1 configuration/BRAM guard
(`CFGBVS-1`, `REQP-1839`, `RBOR-1`) passes. The methodology report retains the same
11 warnings as synthesis: one reset LUTAR alert, six BRAM output-register advisories,
and four wide-multiplier advisories. No new XDC invalid-endpoint warnings were seen.

Evidence files are `opt_timing_summary.rpt`, `opt_drc.rpt`, `opt_methodology.rpt`,
`opt_utilization.rpt`, and `opt_bram_guard.rpt` in the build directory. This
optimization/DRC gate was approved by the user before placement began.

## Placement review gate

Placement completed successfully from `opt.dcp`; Vivado reports 0 placement warnings,
0 critical warnings, and 0 errors. The resulting device use is 8,528 LUTs, 4,493
registers, 4 BRAM tiles, and 48 DSPs. The H16 / 1000 ns TCK preflight passed.

Placement timing has **no setup failures**: WNS +8.867 ns, TNS 0 ns. However, it has
**six hold failures**: WHS -0.086 ns and THS -0.342 ns total. The worst paths are
short register-to-register paths in the requant pipeline (`s1_max` to `s2_max` and
`s1_min` to `s2_min`); two other paths run from NPU data registers to `s1_max`. These
are pre-route estimated delays and require hold repair during routing before final
timing can pass. Do not treat placement as timing-clean or use this checkpoint to
program the FPGA.

Placement DRC has zero errors and the same 75 DSP pipelining advisories. The targeted
configuration/BRAM guard passes. CDC is unchanged: 0 Critical, 2 synchronized toggle
crossings, 3 synchronized resets, and 109 bundled-data `CDC-15` warnings. The
methodology report retains the same 11 warnings, including `LUTAR-1`.

Evidence files: `place_timing_summary.rpt`, `place_hold_paths.rpt`,
`place_clock_interaction.rpt`, `place_drc.rpt`, `place_methodology.rpt`,
`place_cdc.rpt`, `place_utilization.rpt`, and `place_bram_guard.rpt`. The placement
stage completed with hold failures that were subsequently addressed during routing,
as authorized by the user.

## Routing and hold repair review gate

The user authorized routing and hold repair on 2026-10-24. `route_design` ran from
the reviewed `place.dcp`; its built-in Post Hold Fix phase closed the hold violations.
No additional `phys_opt_design` or RTL/constraint changes were needed in this stage.

The final routed timing report meets the documented 25 MHz system / 1 MHz TCK
constraints: WNS **+10.194 ns**, TNS **0 ns**, WHS **+0.033 ns**, THS **0 ns**,
with **zero setup or hold failing endpoints**. Pulse-width checks also pass.
All 14,710 routable nets are fully routed, with zero routing errors, unrouted or
partially routed nets, or node overlaps. Resource use is 8,528 LUTs (41.00%), 4,493
registers, 4 BRAM tiles, and 48 DSPs.

The original six hold paths were queried again in the routed checkpoint:

| Source → destination (relative to NPU) | Placed hold slack (ns) | Routed hold slack (ns) |
|---|---:|---:|
| `i_requant/s1_max[1]` → `i_requant/s2_max[1]` | -0.086 | +0.178 |
| `i_requant/s1_min[5]` → `i_requant/s2_min[5]` | -0.077 | +0.191 |
| `d_reg[9][8]` → `i_requant/s1_max[0]` | -0.069 | +0.199 |
| `d_reg[9][13]` → `i_requant/s1_max[5]` | -0.049 | +0.216 |
| `d_reg[9][14]` → `i_requant/s1_max[6]` | -0.038 | +0.202 |
| `i_requant/s1_min[6]` → `i_requant/s2_min[6]` | -0.024 | +0.163 |

Full DRC remains at zero errors and 75 DSP pipeline advisories; the targeted
configuration/BRAM guard passes. CDC and methodology findings are unchanged from
the reviewed earlier stages (109 bundled-data warnings and the 11 methodology
warnings, including the LUT-driven async-reset alert). No internal unconstrained
endpoints or additional missing IO delays were introduced. Reset/LED exceptions
and the external IO timing assumptions remain as documented above.

Evidence files in the same build directory: `route_timing_summary.rpt`,
`route_status.rpt`, `route_hold_paths.rpt`, `hold_repair_comparison.tsv`,
`route_drc.rpt`, `route_cdc.rpt`, `route_methodology.rpt`, and
`route_clock_interaction.rpt`. Final checkpoint: `route.dcp`, SHA256
`BEE7385AF97B54876D0FC28B5A7A0EA6DA7DCABCEE098F45D0066A47AAF3ACDA`.

The agent's report inspection finds the routing/timing gate meets its criteria
under the documented assumptions. The user approved proceeding to bitstream
generation; no board programming was performed in the routing stage.

## Bitstream generation and inspection gate

The user authorized generation and inspection on 2026-10-24. `bitgen_only.tcl`
opened the reviewed `route.dcp` after its SHA256 was verified. The preflight checked
the complete part, clock periods, external pin assignments, CFGBVS=VCCO,
CONFIG_VOLTAGE=3.3, zero DRC errors, and positive setup/hold slack. `write_bitstream`
completed successfully with 0 warnings, 0 critical warnings, and 0 errors; its
mandatory precondition DRC also reported 0 errors.

Artifact: `build/fpga_ego1_20261024_h16_resetfix_25mhz_v3/fpga_top_h16_25mhz.bit`
(2,192,132 bytes), SHA256:
`6C9A32B9E89F3EBCA44675A3F4835C62751A41E8E32B71651686B8FF3CFC1974`.
The independently parsed .bit container has design `fpga_top`, header device
`7a35tcsg324`, Vivado version 2025.2, a 2,192,012-byte configuration payload whose
declared size matches the file exactly, and an aligned configuration sync word at
payload offset 48. The parsed header omits the speed grade; full target
`xc7a35tcsg324-1` is confirmed by the Vivado checkpoint preflight.

The post-bitgen DRC still has only the same 75 DSP pipeline advisories. Previously
reviewed reset/CDC/IO assumptions remain in force. The container inspection does
not independently validate configuration CRC or establish actual hardware behavior.

Inspection/provenance: `bitstream_manifest.json`, `bitgen_only.log`,
`bitgen_pre_drc.rpt`, and `bitgen_post_drc.rpt` in the same build directory.
Generation and artifact inspection were approved for the diagnostic FPGA download
and fabric JTAG stage below. The configuration flash has not been programmed.

## Diagnostic FPGA programming and JTAG review gate

The user authorized programming and JTAG checks while explicitly retaining 50 MHz
as the formal target. On 2026-10-24 the 25 MHz diagnostic bitstream was loaded into
FPGA SRAM via cable `1234-tulA`. The FPGA identified as `xc7a35t`, IDCODE `0362D093`;
post-programming `DONE=1` and `CRC_ERROR=0`. The programmed bitstream SHA256 is the
same `6C9A32...CFC1974` recorded in the manifest. Flash was not written.

The external FT232H (0403:6014) was forwarded to WSL for OpenOCD. Initial discovery
also listed an unavailable Digilent target; a second enumeration isolated the
known configuration cable and correct FPGA. A first host command test timed out
even though fabric IDCODE readback passed. Local OpenOCD source inspection and raw
scans showed that the existing host supplied unprefixed hex digits to a parser
that defaults to decimal. Thus the READ packet's opcode was not transmitted as 1.
`software/host/nocpu_jtag.py` now sends explicit hexadecimal data/address/opcode
fields (32+32+8 bits) and reconstructs the three returned fields. This changes only
host transport encoding; the programmed bitstream was not rebuilt.

After the fix, both 100 kHz and 1 MHz TCK passed 16 IDCODE reads and 256 register
reads per speed (32 IDCODE reads and 512 register reads total). Results:
fabric IDCODE `0x00000001`, ABI `0x00020000`, activation capacity 4096 bytes, model
capacity 8192 bytes, array 4x8, MAX_K 1024, and MAX_OPS 16. Idle status and error
registers were zero throughout. The configuration TAP IDCODE and fabric TAP IDCODE
belong to different TAPs and are intentionally different.

A 1 MHz write/read test used the idle watchdog register 0x28 with patterns
`0x01234567`, `0x89abcdef`, and `0xa55a5aa5`; all three matched exactly. Its original
value `0x00989680` was restored and checked. Three host transport unit tests passed
for explicit field encoding, response reconstruction, and malformed/out-of-range
responses. No model/input was loaded and no inference was run in this stage.

Evidence under the same build's `hardware/` directory:
`program_diagnostic.log`, `programmed_board_properties.txt`,
`jtag_smoke_result.json`, `jtag_register_roundtrip.json`, and `openocd.log`.
The initial timeout is preserved in `jtag_smoke_result_initial.json`; subsequent
raw traces document its diagnosis. OpenOCD was stopped and the FT232H detached
from WSL after the tests (returned to Windows Shared state). The FPGA retains the
25 MHz diagnostic SRAM image while powered. The bitstream manifest explicitly
records `diagnostic_only`, `release_qualified=false`, and required release clock
50 MHz. This hardware stage is ready for human review; formal 50 MHz timing and
board-level numerical validation are still pending.
