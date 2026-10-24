# FPGA verification result

Overall: **PASS**; functional: **PASS**; physical: **PASS_WITH_ASSUMPTIONS**.

- Board: 43 cases, 190 inference jobs, 26365 firmware checks, 684 IRQ checks.
- Recovery: 6 full-SRAM patterns, 6 resets (3 active-workload interruptions), 12 additional inference jobs.
- RTL: 7 unit runs, 21 golden cases, 33936 element comparisons; SoC/DMA/SRAM structure regression PASS.
- Routed timing: WNS 0.913 ns, WHS 0.013 ns.
- External JTAG paths: minimum setup 448.044 ns, hold 472.005 ns; effective min/max delays and equivalent clock edges checked.
- Nominal JTAG frequency sweep: 10/100/500/1000 kHz; 8 trials x16 random SRAM words and BootROM at each setting; scratch restored.
- IO timing scope: FT232H nominal TCK<=1MHz, rising sample, <=30cm direct 3.3V wiring; propagation0..5ns and TMS output envelope are unmeasured assumptions; external edge guard20ns. Electrical measurements remain outside this acceptance.
- Bitstream SHA256: `d45dc5ee146f71a7247886d1eba462391ea139ba0de283db4d85122af848e368`.

## Unresolved signoff gates

- None within the declared scope.

## Coverage limits

- Finite directed/random regression is not an exhaustive RTL proof.
- Board comparison observes logits; intermediate layers are checked in RTL simulation.
- Default trained-model frames are synthetic features, not real speech accuracy evidence.
- CPU ISA completeness, pin-level electrical margins and arbitrary AXI interleavings are not established.
- External IO STA passes only under the declared short-wire timing envelope; TMS timing and propagation assumptions are not oscilloscope measurements or signal-integrity/PVT certification.
- IRQ pending/clear is observed through CPU mip; interrupt trap entry, ISR and mret are not exercised.
- Reset trials use DM ndmreset, not physical button/PLL loss or cycle-exact NPU busy timing.
- No post-route timing simulation, formal equivalence, exhaustive ISA compliance, voltage/temperature sweep or functional/code coverage closure.
- Bitstream/checkpoint association uses the selected accepted build, programming receipt and hashes; not physical configuration readback proof.
