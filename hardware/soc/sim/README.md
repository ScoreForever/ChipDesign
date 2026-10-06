# SoC Simulation

This directory is a lightweight simulation entry for the current SoC
integration. It reuses the CPU, AXI, debug, boot, and memory building blocks
under `cpu_cv32e40p/` and `soc/rtl/`, while the NPU side is connected from
`simple_npu/rtl/` through `soc/rtl/my_npu_subsystem.sv`.

The default top-level testbench is `my_soc_tb`. The program image loaded into
SRAM at time 0 is selected by `INIT_FILE` in `soc/rtl/mem/my_mainmem.sv`
(default `soc/sim/tb/lab3_test1.hex`); the matching C self-check sources are
`soc/sim/tb/lab3_test{1,2,3}.c`.

## ModelSim flow

Run from the repository root (`SoC_cv32e40p/`):

```tcl
# one-time setup
vlib work
vmap work work
file mkdir soc/sim/out

# compile design + tb (filelist resolves paths relative to cwd)
vlog -sv -f soc/sim/filelists/my_soc_tb.f

# elaborate + run; -novopt avoids a known vopt segfault on lzc.sv in 2020.4
vsim -suppress 12110 -novopt work.my_soc_tb
run -all
```

Incremental rebuild after touching one RTL file:

```tcl
quit -sim
vlog -sv <changed_file>.sv
vsim -suppress 12110 -novopt work.my_soc_tb
run -all
```

The testbench polls a magic region at the top of SRAM (`0x80001FE0`) and
prints `PASS` / `FAIL case=N @ (i,j)` / `TIMEOUT` based on what the test
program writes there. See `soc/sim/tb/my_soc_tb.sv` for the layout.

Note: `my_soc_tb.sv` always dumps every signal to `soc/sim/out/my_soc_tb.vcd`.
That file is large and slows the run down. See `sim/tb/my_soc_tb.sv` in the
Lab 2 package for one way to make the dump optional.
