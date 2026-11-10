Overall: **BLOCKED**; functional: **PASS**; physical: **BLOCKED**.
## Unresolved signoff gates
## Coverage limits
# FPGA verification result
- TIMING-18: external IO delay assumptions are incomplete
- RTL: 7 unit runs, 21 golden cases, 33936 element comparisons; SoC/DMA/SRAM structure regression PASS.
- Routed timing: WNS 0.758 ns, WHS 0.013 ns.
- Reset trials use DM ndmreset, not physical button/PLL loss or cycle-exact NPU busy timing.
- Recovery: 6 full-SRAM patterns, 6 resets (3 active-workload interruptions), 12 additional inference jobs.
- No post-route timing simulation, formal equivalence, exhaustive ISA compliance, voltage/temperature sweep or functional/code coverage closure.
- IRQ pending/clear is observed through CPU mip; interrupt trap entry, ISR and mret are not exercised.
- Finite directed/random regression is not an exhaustive RTL proof.
- Default trained-model frames are synthetic features, not real speech accuracy evidence.
- CPU ISA completeness, pin-level electrical margins and arbitrary AXI interleavings are not established.
- Board: 43 cases, 190 inference jobs, 26365 firmware checks, 684 IRQ checks.
- Board comparison observes logits; intermediate layers are checked in RTL simulation.
- Bitstream/checkpoint association uses the selected accepted build, programming receipt and hashes; not physical configuration readback proof.
- Bitstream SHA256: `daf0bdd6f7e264864b3203330848f612f1e13afe50e39d5b875673977f237929`.






