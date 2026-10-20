# FPGA verification result

Overall: **BLOCKED**; functional: **PASS**; physical: **BLOCKED**.

- Board: 43 cases, 190 inference jobs, 26365 firmware checks, 684 IRQ checks.
- Recovery: 6 full-SRAM patterns, 6 resets (3 active-workload interruptions), 12 additional inference jobs.
- RTL: 7 unit runs, 21 golden cases, 33936 element comparisons; SoC/DMA/SRAM structure regression PASS.
- Routed timing: WNS 0.758 ns, WHS 0.013 ns.
- Bitstream SHA256: `daf0bdd6f7e264864b3203330848f612f1e13afe50e39d5b875673977f237929`.

## Unresolved signoff gates

- TIMING-18: external IO delay assumptions are incomplete

## Coverage limits

- Finite directed/random regression is not an exhaustive RTL proof.
- Board comparison observes logits; intermediate layers are checked in RTL simulation.
- Default trained-model frames are synthetic features, not real speech accuracy evidence.
- CPU ISA completeness, pin-level electrical margins and arbitrary AXI interleavings are not established.
- IRQ pending/clear is observed through CPU mip; interrupt trap entry, ISR and mret are not exercised.
- Reset trials use DM ndmreset, not physical button/PLL loss or cycle-exact NPU busy timing.
- No post-route timing simulation, formal equivalence, exhaustive ISA compliance, voltage/temperature sweep or functional/code coverage closure.
- Bitstream/checkpoint association uses the selected accepted build, programming receipt and hashes; not physical configuration readback proof.
