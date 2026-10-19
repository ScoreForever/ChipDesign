# no_cpu die autosize diagnosis (2026-10-18)

## Conclusion

The current `no_cpu` implementation is **core/routing limited before it is pad-ring limited** for the course 2 mm x 2 mm target.

## Experiment A: practical reduced die

- Core request: 2100 um x 2100 um
- Core area: 4.410000 mm2
- Margin: 150 um per side
- Actual die: 2400.160 um x 2400.160 um
- Die area: 5.760768 mm2
- Pad/corner/filler floorplan: completed with 0 floorplan errors
- Final non-filler placement density: 57.74%
- Post-route setup WNS/TNS: +0.861 ns / 0.000 ns
- Post-route hold WNS/TNS: +0.020 ns / 0.000 ns
- Regular connectivity: 0 violations
- Core VDD/VSS connectivity: 0 violations
- Innovus DRC: 0 violations
- Process antenna: 5 violations
- DEF/netlists/SPEF/SDF/LEF/GDS exports: generated
- Post-layout simulation: not rerun for this exploratory die
- Status: successful routed area experiment, not a clean formal result because antenna is not zero

Main log:

`pnr_no_cpu_autosize_20261018/pnr/logs/autosize_2100_full.log`

## Experiment B: 2 mm x 2 mm target test

- Core request: 1700 um x 1700 um
- Margin: 150 um per side
- Actual die: 2000.320 um x 2000.320 um
- Die area: 4.001280 mm2
- Pad/corner/filler floorplan: completed with 0 floorplan errors and no pad outside the die
- Initial core utilization: 81.4915%
- Initial mapped cell area / allocatable row area: 2.355548 / 2.885784 mm2
- Placement optimization density: approximately 84.8%
- Early global-route overflow after optimization: approximately 3.7-5.9% horizontal and 1.6-2.8% vertical
- Explicit optimizer failure reason: 24 nets, later 7143 nets, could not be fixed because of `exceeding max density`
- Run stopped before CTS because the core was already not physically closable

Logs:

- `pnr_no_cpu_autosize_core1700_20261018/pnr/logs/autosize_1700_stage1.log`
- `pnr_no_cpu_autosize_core1700_20261018/pnr/logs/autosize_1700_place.log`

## Interpretation

- The original 3.100 mm die was not required by the pad count. It was preserved by changing margins while shrinking the core.
- The existing pad ring can be repacked inside an approximately 2.000 mm die at the LEF/floorplan level.
- The 1700 um core required by a 2 mm die is too dense for this mapped no-CPU RTL before CTS and final routing overhead are added.
- Therefore the next meaningful die reduction requires reducing NPU mapped area and clock/routing demand, especially flop-based weight, activation, psum, parameter and window storage.

## Scope

These are Innovus exploratory results. They do not replace the formal 2800 um clean flow and do not constitute Calibre/foundry signoff.
