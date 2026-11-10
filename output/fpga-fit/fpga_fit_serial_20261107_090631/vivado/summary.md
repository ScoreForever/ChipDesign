# EGo1 build summary

Result: FAILED
Stage: acceptance; last completed stage: write_bitstream
Part: xc7a35tcsg324-1; top: fpga_top; CPU-less JTAG controller and programmable five-operator NPU
Clocking Wizard: 100 MHz input, 50 MHz output, locked enabled, reset disabled
Log: vivado.log

Failure: Timing failed: WNS=-1.694 ns, WHS=0.043 ns

## Resources (latest available stage)

| Resource | Used | Available | Utilization (%) |
|---|---:|---:|---:|
| Slice LUTs | 18516 | 20800 | 89.02 |
| Slice Registers | 6931 | 41600 | 16.66 |
| Block RAM Tile | 4 | 50 | 8.00 |
| DSPs | 12 | 90 | 13.33 |
| MMCME2_ADV | 1 | 5 | 20.00 |

## Timing (latest available stage)

WNS: -1.694 ns; TNS: -1116.673 ns; WHS: 0.043 ns

## Main SRAM mapping

N/A: CPU-less system uses NPU-local storage; no main SRAM

## Configuration/BRAM DRC

PASS at route: CFGBVS-1, REQP-1839, RBOR-1

## JTAG clock routing

Not applied
