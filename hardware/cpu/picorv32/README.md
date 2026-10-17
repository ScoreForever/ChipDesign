# PicoRV32 integration

Vendored upstream: https://github.com/YosysHQ/picorv32

Pinned commit: `ef203c2b0a3fb793280f5114941416c425c5b461`.
`picorv32.v`, `COPYING`, and `README.upstream.md` are unmodified upstream files.
The upstream core is ISC licensed; retain COPYING and its source notices.

`picorv32_soc.sv` is the project integration, not an upstream modification.
Configuration: RV32IMC, 32 integer registers, single-read-port register file,
iterative multiplier/divider, no fast multiplier, 64-bit cycle/instret counters,
Pico IRQs with q-registers, no timer. The instruction and data paths share one
single-outstanding AXI master. Addresses are word aligned; SB/SH byte lanes are
carried by WSTRB. AW and W are independent and the CPU waits for B, not just
address acceptance. AXI errors latch trap status and stop the core.

The bridge captures requests and drains accepted transactions even when the
CPU run bit is cleared. CPU reset does not reset SRAM, DMA, NPU or the fabric.
Re-enabling run waits until the bridge is empty, then restarts at `0x00010000`.

## Software compatibility

BootRAM still jumps to `0x80000000`; SRAM remains 8 KiB and the NPU MMIO ABI
is unchanged. Build firmware with `-march=rv32imc -mabi=ilp32`. Read `cycle`
(`0xc00`), not `mcycle` (`0xb00`). Do not write `mcountinhibit` (`0x320`).
This core does not implement machine-mode privileged CSRs or RISC-V Debug
Mode. Previously compiled firmware using those features must be rebuilt.

IRQ16=NPU, IRQ17=DMA. The IRQ vector is SRAM+0x10. IRQs start masked, so
existing polling firmware needs no handler. Interrupt firmware must reserve
that vector and use Pico's `maskirq/getq/setq/retirq` instructions, not
`mstatus/mtvec/mret`. See `gen_picorv32_test.py` for an executable example.

## JTAG and program loading

The existing JTAG DTM and Debug Module **system bus access (SBA)** remain.
The hart is reported unavailable because native CPU halt/resume/register
access/single-step are not implemented. An unchanged OpenOCD/GDB hart-debug
configuration is therefore not supported. Use DMI SBA transactions instead.

CPU control registers occupy two formerly unused BootRAM words:

| Address | Access | Meaning |
| --- | --- | --- |
| `0x00010038` | read | bit0 stopped (CPU reset and AXI drained), bit1 trap, bit2 requested run |
| `0x0001003c` | read/write | bit0 run; write 0 to reset CPU, 1 to restart from BootRAM |

The FPGA top sets `CPU_BOOT_HOLD=1`: after configuration/reset the CPU waits
for a program, while JTAG SBA and the bus remain operational. Standalone
SoC simulation defaults to `CPU_BOOT_HOLD=0` for the existing preloaded tests.

Loading sequence: activate DM (DMI 0x10=1), use 32-bit SBA, write CPU_RUN=0,
poll CPU_STATUS.stopped, write firmware into SRAM, then CPU_RUN=1. To reload,
repeat this sequence. This resets execution rather than resuming a saved PC.
Use SBCS (`0x38`), SBADDRESS0 (`0x39`) and SBDATA0 (`0x3c`); wait for SBCS
sbbusy=0 and check error flags between operations. For writes SBCS=0x40000;
for read-on-address SBCS=0x140000. This sequence is tested at the DMI boundary;
physical JTAG/debugger and board programming are not claimed by simulation.

## Regression

From the repository root, with ModelSim on PATH:

```powershell
python hardware/soc/sim/sw/gen_picorv32_test.py
python hardware/soc/sim/scripts/run_picorv32_regression.py --config both
```

The runner uses an ASCII temporary source snapshot for ModelSim on Windows
and preserves logs in a new work/ directory without deleting existing work.
It checks the original CPU/NPU and DMA programs plus ISA/IRQ/SBA/restart tests,
requantization, activation capacity and AXI SRAM behavior. Add repeatable
`--workload build/<directory>` options to run compiled C workloads and compare
every raw logit and predicted class against Python.

The raw-DMI OpenOCD loader is `software/tools/picorv32_sba.tcl`. Configure the
board's adapter/TAP, source that file, call `pico_sba_init <tap-name>`, then
`pico_load_hex <workload.hex>`. It waits for accelerator completion and verifies
the entire image before starting the CPU. Physical JTAG remains unvalidated.

Measured migration results and reproduction details:
[2026-10-17 report](../../../docs/PICORV32_MIGRATION_20261017.md).
