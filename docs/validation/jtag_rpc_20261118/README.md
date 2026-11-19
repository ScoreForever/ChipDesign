# Week 3 JTAG Burst & Single-RPC Pipelining Evidence

## Evidence Files

- `rtl_1mhz_7models.json`: **Directly measured** `1000 ns` (`1.0 MHz TCK`) Icarus Verilog 13.0 physical-JTAG wire simulations (`tb_npu_program.sv`, `JTAG_TEST=1`) across all 7 pretrained models in `IR 13h/14h` burst mode (`PASS program`, zero fatal/mismatch errors), plus completed `1.0 MHz` wordwise-vs-burst anchors (`ecg`, `har`) and matched `16 ns` TCK ratios from `e2e_timing.json`. **Zero estimates or extrapolated wall times.**
- `tcl_tcp_sim.json`: **Directly measured** localhost TCP (`TclRpc` `0x1A` protocol) + persistent `tclsh` OpenOCD-protocol simulation (`software/zoo/jtag_tcl_sim_bench.py`) executing unmodified `nocpu_jtag.Npu` (`chipdesign_reads_v5` + `chipdesign_load_v5` for `load_model`; `chipdesign_infer_v5` + Option A trailing `Op.ACK` for resident `infer`), verifying **7/7 bit-exact output equality** and identical `drscan` counts (`median` of 5 warm runs per mode).
- `e2e_timing.json` & `ecg_1mhz_anchor.json`: Historical RTL timing archives from `software/zoo/e2e_timing.py` (`16 ns` fast-sim sweep and `ecg` `1000 ns` anchor). Note: those two older files also include a legacy `estimated_rpc_ms` convenience field (`4.0 ms/RPC`); **all Week 3 slide claims strictly exclude estimates** and cite only the measured `rtl_1mhz_7models.json` and `tcl_tcp_sim.json` evidence below (physical EGo1 board validation for combined SRAM-lookup + Single-RPC remains open for Phase B).

## Measured Simulation Summary (All 7 Pretrained Models, Bit-Exact)

| Model | Bytes (M / In / Out) | Compute Cycles | 1 MHz RTL Burst (ms) | Upload Share | 16 ns Word/Burst TCK | Batched → Pipe RPCs | RPC Cut | Identical `drscan`s | TCP+tclsh (ms) | Loopback × |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ecg` | 3908 / 64 / 20 | 13,247 | 42.903 | 89.0% | 5.43x | 54 → 4 | 13.50x | 1,079 | 68.44 → 8.66 | 7.90x |
| `fashion_cnn` | 8104 / 392 / 40 | 35,765 | 85.158 | 90.4% | 5.85x | 71 → 4 | 17.75x | 2,215 | 79.71 → 13.54 | 5.89x |
| `gtsrb_l` | 7736 / 2048 / 172 | 78,990 | 98.858 | 74.5% | 5.87x | 76 → 4 | 19.00x | 2,570 | 93.70 → 15.09 | 6.21x |
| `har` | 4216 / 576 / 24 | 15,357 | 50.529 | 81.2% | 5.58x | 57 → 4 | 14.25x | 1,285 | 80.79 → 10.92 | 7.40x |
| `kws_l` | 7896 / 245 / 48 | 71,318 | 82.340 | 91.2% | 5.82x | 69 → 4 | 17.25x | 2,129 | 98.06 → 12.27 | 7.99x |
| `kws_m` | 7632 / 245 / 48 | 72,935 | 79.914 | 90.9% | 5.80x | 68 → 4 | 17.00x | 2,063 | 83.57 → 12.81 | 6.53x |
| `mnist_l` | 4688 / 392 / 40 | 30,184 | 53.505 | 84.9% | 5.60x | 58 → 4 | 14.50x | 1,361 | 74.45 → 12.53 | 5.94x |

## Direct `1.0 MHz TCK` Wordwise-vs-Burst RTL Anchors

| Model | Wordwise Cmds | Burst Cmds | Wordwise (ms) | Burst (ms) | Measured Wire Speedup |
|---|---:|---:|---:|---:|---:|
| `ecg` | 1022 | 27 | 235.851 | 42.903 | 5.50x |
| `har` | 1228 | 27 | 283.437 | 50.529 | 5.61x |
