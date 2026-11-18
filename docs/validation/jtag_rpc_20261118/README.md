# Week 3 JTAG Pipeline RPC Evidence (`perf/jtag-single-rpc`)

- **Protocol change**: `chipdesign_reads_v5` + `chipdesign_load_v5` (2-RPC `load_model`), `chipdesign_infer_v5` + Option A trailing `Op.ACK` (2-RPC resident `infer`), plus `TclRpc` fail-closed socket poisoning and monotonic deadline.
- **Estimation boundary**: `estimated_rpc_ms` applies the historical `4.0 ms/RPC` OpenOCD baseline estimate (`e2e_timing.json`); this run is an RTL/Tcl simulator measurement (Icarus Verilog 13.0 + Tcl 8.6), **not** an EGo1 physical board measurement.
- **Cycle-to-Driver RPC reconciliation**: `e2e_timing.py` emits `17-24` burst infer RPCs (skipping `read(0x18/0x1c)` because manifest is already held), whereas bare `Npu.infer()` includes those 2 address/length reads (`19-26` RPCs at `P=1`). Both collapse to **2 RPCs** under `chipdesign_infer_v5` + `Op.ACK`.

| Model | Model (B) | In/Out (B) | Compute Cycles | Batched Load+Infer RPCs | Pipeline Load+Infer RPCs | Batched Est. (ms) | Pipeline Est. (ms) | Total RPC Speedup |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `ecg` | 3908 | 64/20 | 13247 | 35+17=52 | 2+2=4 | 208.0 | 16.0 | 13.0x |
| `fashion_cnn` | 8104 | 392/40 | 35765 | 51+18=69 | 2+2=4 | 276.0 | 16.0 | 17.2x |
| `gtsrb_l` | 7736 | 2048/172 | 78990 | 50+24=74 | 2+2=4 | 296.0 | 16.0 | 18.5x |
| `har` | 4216 | 576/24 | 15357 | 36+19=55 | 2+2=4 | 220.0 | 16.0 | 13.8x |
| `kws_l` | 7896 | 245/48 | 71318 | 50+17=67 | 2+2=4 | 268.0 | 16.0 | 16.8x |
| `kws_m` | 7632 | 245/48 | 72935 | 49+17=66 | 2+2=4 | 264.0 | 16.0 | 16.5x |
| `mnist_l` | 4688 | 392/40 | 30184 | 38+18=56 | 2+2=4 | 224.0 | 16.0 | 14.0x |
