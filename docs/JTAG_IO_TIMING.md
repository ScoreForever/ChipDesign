# EGo1 FT232H JTAG timing assumptions

The programmable NPU is controlled through the FPGA fabric JTAG TAP. This is
separate from the FPGA configuration TAP. The current XDC profile targets the
local EGo1 V2.2 wiring and an FT232H in MPSSE mode at no more than 1 MHz, with
the host sampling TDO on rising TCK.

The official [FT232H datasheet, MPSSE timing table](https://ftdichip.com/wp-content/uploads/2024/09/DS_FT232H.pdf)
specifies CLKOUT-to-data delay of 0–7.5 ns, data-input setup of 11 ns, and
input hold of 0 ns at the 30 MHz reference waveform. The board-level profile
adds an assumed 0–5 ns propagation envelope for short direct wires/connectors
and a 20 ns edge guard. The 0–5 ns propagation and guard are engineering
assumptions, not measured electrical limits.

This gives the XDC input envelope for TDI/TMS of -25..32.5 ns around falling
TCK and output envelope for TDO of -20..41 ns around rising TCK. Equivalent
half-cycle constraints are also included because the TAP launches TDO on the
falling edge. These duplicate expressions preserve the same physical window.

The local board's TCK wire is assigned to H16 (EGo1 J5 pin 21). Earlier
boundary-SAMPLE measurements found the local E15/J5 pin 17 path did not toggle.
The reset button is asynchronous and is checked through recovery tests; the
status LEDs have no synchronous receiving device. Functional board results
must retain these timing assumptions and do not constitute full PVT or analog
signal-integrity signoff.
