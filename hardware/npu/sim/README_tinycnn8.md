# TinyCNN-8 reproducible RTL and MMIO experiment

Run from the repository root:

```sh
python3 hardware/npu/scripts/run_tinycnn8_regression.py --output /path/to/new-empty-directory
```

Python 3, Icarus Verilog and vvp are required. No training framework, Swift,
model download or historical Git object is required for default regression.
The shell wrapper is available as `sh hardware/npu/scripts/run_tinycnn8_regression.sh`.
The default output lives under `hardware/npu/sim/tinycnn8_tiled/`; an existing
run is preserved in place and subsequent results use a timestamped child.

The full run tests original/overlap/tile7/8/16/32 schedules with the same 4x8
Matrix core, official quantization contract and synthetic model fixtures.
It includes integer-oracle tests, standalone layer outputs, module backpressure
and lifecycle tests, official MMIO smoke, and file-driven MMIO tasks.
Additional fixed-seed cases exercise tiled schedules with varied class counts.
`--quick` reduces network cases; `--skip-units` is a development smoke option,
not an accepted full experiment. The report generator rejects skipped units.

## Evidence

- `summary.json`: mode parameters, correctness results, trace metrics, hardware
  profile, regressions, tool versions and source hashes.
- `comparison.csv`: observed trace and hardware cycle columns are distinct.
- `cases/<case>/data`: input/model/golden hex and fixture manifest.
- `cases/<case>/<mode>`: handshake events, complete output coverage, state
  statistics, independent profiler comparison and logs.
- `cases/<case>/mmio_<mode>`: production MMIO writes, three consecutive inference
  jobs, logits and hardware profile readback.
- `run_manifest.json`: result file hashes.

Conv2 tiled weight loads should be `4*18*ceil(80/T)` rows, with unchanged
1,440 Matrix inputs/outputs. Conv1 and FC use fallback paths. The new observer
counts activation, logical weight consumption and Matrix load events separately;
there is no one-to-one window/load assumption in the reused schedule.

Profiler counts START/WAIT sequencer states, excluding accepted-start IDLE,
including completion transitions. Layer profile sum equals profile total absent
saturation. Testbench layer-event intervals are a separate metric; do not combine
the two kinds of layer counts. Host load cycles are not core, CPU or AXI latency.

## Report generation

```sh
python3 hardware/npu/tools/build_tiled_report.py /path/to/accepted-results --output docs/kws_tinycnn8_tiled
swift hardware/npu/tools/render_tiled_charts.swift docs/kws_tinycnn8_tiled/performance_summary.csv docs/kws_tinycnn8_tiled
```

Swift/AppKit only renders charts on macOS; it is not a simulation dependency.
The old `docs/kws_tinycnn8` report remains historical phase-one evidence, not
an up-to-date deployment guide or newest-main experiment. Use the tiled report.

## Boundaries

This experiment verifies a fixed arithmetic network with synthetic weights and
features. It does not establish trained KWS accuracy. Production MMIO backend
is exercised directly; CPU, AXI, DMA and full SoC are not simulated here.
The activation/weight arrays use independent combinational logical reads.
Synchronous SRAM/BRAM, implementation PPA and real clock rates remain unverified.
Tile buffers and tag queue capacity are logical declaration counts, not area.
No energy claim follows solely from fewer weight load events.

Source and compact results can be reviewed in Git; full trace, VCD and build
artifacts remain generated local/CI evidence. CI uploads artifacts instead of
adding megabytes of duplicate trace to the repository.
