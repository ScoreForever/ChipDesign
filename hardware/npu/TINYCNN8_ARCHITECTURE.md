# TinyCNN-8 implemented NPU architecture

Checked against `main` commit `7569a43` on 2026-10-14. The implemented core uses
GAP; the software exporter uses Flatten. The MMIO adapter also retains Flat-era
parameter restrictions. See [system design details](../../docs/ARCHITECTURE.md)
for the complete register map and the integration gap.

## 1. Fixed compute graph

Activations are signed INT8 in NHWC order, channel fastest.

| Stage | Operation | Output |
| --- | --- | --- |
| Input | Host-provided features | `20×16×1` INT8 |
| Conv1 | `3×3 SAME, 1→8`, bias/requant/ReLU | `20×16×8` INT8 |
| Pool1 | `2×2 stride2` MaxPool | `10×8×8` INT8 |
| Conv2 | `3×3 SAME, 8→8`, bias/requant/ReLU | `10×8×8` INT8 |
| Pool2 | `2×2 stride2` MaxPool | `5×4×8` INT8 |
| GAP | Sum 20 spatial values/channel, then requant | 8 INT8 |
| FC | `1×1 Conv, 8→class_count` | 1–8 INT32 logits |

No Flatten stage is implemented. The core has no label names or frontend DSP.
The four-class Flat software package (`160→4`) cannot be loaded directly.

## 2. Shared datapath and sequencing

```text
Conv1/Conv2/FC -> conv2d_engine -> window generator -> Matrix Unit
Pool1/Pool2   -> maxpool2x2_engine -> Vector Unit
GAP           -> global_avg_pool_engine -> global_sum_pool_engine -> reduction
Conv/GAP      -> one shared serial requant_unit
```

The default Matrix Unit is 4×8, with INT8 activations/weights and INT32 partial
sums. Bias initializes Conv/FC partial sums. FC selects INT32 output and bypasses
requant. The sequencer executes C1, P1, C2, P2, GAP and FC serially, each with a
START handshake and WAIT state. Accepted start clears logits and latches the
class count; completion emits a one-cycle done pulse.

`OPT_GATHER_LOAD=0` and `OPT_SPATIAL_TILE=0` are defaults, including the current
SoC. `SPATIAL_TILE=16` sets capacity when enabled. Optimization applies only to
the fixed 4×8, `10×8×8`, `3×3 SAME`, stride1 INT8 Conv2 descriptor. Spatial tiling
takes priority over gather/load overlap; other descriptors fall back.
K-major tiling reuses loaded weights across output positions and tags retired
partial sums. Tile16 declares 512 B of INT32 partial sums plus packs/tags.
Matrix itself has no weight double buffer or simultaneous compute/load.

## 3. Integer behavior

- Conv/ReLU clamps to `[0,127]`; GAP clamps to `[-128,127]`; offset is zero.
- GAP multiplier/shift must include division by 20 and the output scale change.
- Per-channel Q0.31 scaling uses TFLite/gemmlowp double rounding. The high
  multiply is `product[62:31] + product[30]`, with the INT32_MIN×INT32_MIN
  saturation exception; right-shift rounding uses ties away from zero.
- Positive shifts saturate before multiplication. INT32 bias/accumulator
  additions do not saturate; model parameters must avoid overflow.
- One vector is latched and processed in four phases per lane. With eight
  lanes, out_valid is produced 32 cycles after acceptance; output backpressure
  holds the result. Masked lanes still take phases and produce zero.
- FC emits raw INT32; any per-class scale alignment is a host/model contract.

## 4. Memory layout

```text
input A(320 B) -> C1 B(2560 B) -> P1 A(640 B)
 -> C2 B(640 B) -> P2 A(160 B) -> GAP B(8 B) -> FC logits
```

The standalone core defaults to 4096 B per activation area. The MMIO wrapper
sets A=640 B and B=2560 B. Each area contains eight byte banks, addressed by
`bank=address%8`, `row=address/8`, with synchronous writes and asynchronous
reads. These are behavioral arrays, not ASIC SRAM macros.

For eight columns, weight memory is 256×64 bit; lane 0 occupies the lowest byte.

| Layer | Base | Active words | Range |
| --- | ---: | ---: | --- |
| Conv1 | 0 | 9 | `[0,9)` |
| Conv2 | 32 | 72 | `[32,104)` |
| FC | 192 | 8 | `[192,200)` |

There are 89 active words with gaps between layers. General addressing is
`base + k*ceil(output_channels/ARRAY_COLS) + output_tile`; four-column cores
use additional output tiles. Parameters are indexed by output tile: layer 0/1
use bias/multiplier/shift, layer 2 (GAP) uses multiplier/shift, layer 3 uses bias.
Weight/parameter arrays are not reset-initialized. Reload 320 input bytes for
each inference; weights and parameters can persist across jobs.

## 5. Profiler and MMIO limitation

Profiler lanes are C1/P1/C2/P2/GAP/FC; lane 4 actively counts GAP. START/WAIT
edges count, including completion transitions, excluding accepted-start IDLE.
Counters saturate, freeze on completion and clear on accepted start/reset.
The wrapper latches done for IRQ and exposes version `0x0002_0001`.

Native host ports can load layer 2, but MMIO layer 2 commit returns error 6.
GAP parameters are consequently unavailable through the current production
adapter and are not initialized by reset. The version constant does not imply
Flat compatibility. Software exports bases 0/9/81 and a 160-input FC; it must not
be treated as a deployment package for this core.

## 6. Verification boundary

`tb_tinycnn8_gap_capacity.sv` directly loads all four parameter groups and
checks nonzero GAP networks with 4/6/8 classes and both activation capacities.
Existing ModelSim structure/DRC reports record those checks, Q31 arithmetic,
SoC zero-model smoke, DMA and AXI/SRAM tests. See the linked system document.
Flat trained-model results and earlier schedule measurements are historical
and do not establish current GAP MMIO deployment correctness. This documentation
update did not rerun simulation or synthesis.
