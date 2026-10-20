# FPGA verification result

Overall: **BLOCKED**; functional: **PASS**; physical: **BLOCKED**.

- Board: 43 cases, 190 inference jobs, 26365 firmware checks, 684 IRQ checks.
- Recovery: 6 full-SRAM patterns, 6 resets (3 active-workload interruptions), 12 additional inference jobs.
- RTL: 7 unit runs, 21 golden cases, 33936 element comparisons; SoC/DMA/SRAM structure regression PASS.
- Routed timing: WNS 1.053 ns, WHS 0.009 ns.
- Bitstream SHA256: `015e93fa87080f09380b687ffbdc8c5923483fc7e443555280c049a12e02a316`.

## Unresolved signoff gates

- CDC-7: 23 critical CDC paths require closure
- CDC-2: acknowledge synchronizers lack ASYNC_REG properties
- TIMING-18: external IO delay assumptions are incomplete
- Asynchronous clock groups cut DMI bundled-data paths; cdc_2phase documented max-delay requirement has no explicit constraint

## Coverage limits

- Finite directed/random regression is not an exhaustive RTL proof.
- Board comparison observes logits; intermediate layers are checked in RTL simulation.
- Default trained-model frames are synthetic features, not real speech accuracy evidence.
- CPU ISA completeness, pin-level electrical margins and arbitrary AXI interleavings are not established.
- IRQ pending/clear is observed through CPU mip; interrupt trap entry, ISR and mret are not exercised.
- Reset trials use DM ndmreset, not physical button/PLL loss or cycle-exact NPU busy timing.
- No post-route timing simulation, formal equivalence, exhaustive ISA compliance, voltage/temperature sweep or functional/code coverage closure.
- Bitstream/checkpoint association uses the selected accepted build, programming receipt and hashes; not physical configuration readback proof.
