# EGo1 build summary

Result: FAILED
Stage: place; last completed stage: opt
Part: xc7a35tcsg324-1; top: fpga_top; CPU-less JTAG controller and programmable five-operator NPU
Clocking Wizard: 100 MHz input, 50 MHz output, locked enabled, reset disabled
Log: vivado.log

Failure: ERROR: [Common 17-39] 'place_design' failed due to earlier errors.


## Resources (latest available stage)

| Resource | Used | Available | Utilization (%) |
|---|---:|---:|---:|
| Slice LUTs* | 33901 | 20800 | 162.99 |
| Slice Registers | 10928 | 41600 | 26.27 |
| Block RAM Tile | 4 | 50 | 8.00 |
| DSPs | 12 | 90 | 13.33 |
| MMCME2_ADV | 1 | 5 | 20.00 |

## Timing (latest available stage)

WNS: -6.898 ns; TNS: -4477.917 ns; WHS: 0.045 ns

## Main SRAM mapping

N/A: CPU-less system uses NPU-local storage; no main SRAM

## Configuration/BRAM DRC

PASS at opt: CFGBVS-1, REQP-1839, RBOR-1

## JTAG clock routing

Not applied
