# EGo1 build summary

Result: FAILED
Stage: acceptance; last completed stage: write_bitstream
Part: xc7a35tcsg324-1; top: fpga_top; CPU-less JTAG controller and programmable five-operator NPU
Clocking Wizard: 100 MHz input, 50 MHz output, locked enabled, reset disabled
Log: vivado.log

Failure: Timing failed: WNS=-0.277 ns, WHS=0.020 ns

## Resources (latest available stage)

| Resource | Used | Available | Utilization (%) |
|---|---:|---:|---:|
| Slice LUTs | 17058 | 20800 | 82.01 |
| Slice Registers | 10348 | 41600 | 24.88 |
| Block RAM Tile | 8 | 50 | 16.00 |
| DSPs | 14 | 90 | 15.56 |
| MMCME2_ADV | 1 | 5 | 20.00 |

## Timing (latest available stage)

WNS: -0.277 ns; TNS: -7.127 ns; WHS: 0.020 ns

## Main SRAM mapping

N/A: CPU-less system uses NPU-local storage; no main SRAM

## Configuration/BRAM DRC

PASS at route: CFGBVS-1, REQP-1839, RBOR-1

## JTAG clock routing

Not applied
