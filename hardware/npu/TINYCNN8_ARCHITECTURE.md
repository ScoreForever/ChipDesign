# KWS TinyCNN-8-Flat NPU Architecture Baseline

Status: current compute-core baseline. The authoritative SoC register map and
software/hardware boundary are in `docs/ARCHITECTURE.md`.

## 1. Accelerator boundary

The host converts one second of 16 kHz audio to a signed INT8 `20×16×1`
log-mel tensor. The NPU returns four signed INT32 raw logits for
`yes/no/up/down`. Audio capture, log-mel, final per-class scale alignment,
argmax, rejection policy, and application behavior are software tasks.

Tensor storage is NHWC: channel is the fastest-changing dimension.

## 2. Fixed production network

| Stage | Operation | Output shape |
| --- | --- | --- |
| Input | signed INT8 feature tensor | `20×16×1` |
| Conv1 | `3×3`, stride 1, SAME, `1→8`; bias/requant/ReLU | `20×16×8` INT8 |
| Pool1 | `2×2`, stride 2 MaxPool | `10×8×8` INT8 |
| Conv2 | `3×3`, stride 1, SAME, `8→8`; bias/requant/ReLU | `10×8×8` INT8 |
| Pool2 | `2×2`, stride 2 MaxPool | `5×4×8` INT8 |
| Flatten | reinterpret contiguous NHWC storage | `160` INT8 |
| FC | `160→4` | `4` INT32 logits |

Flatten contains no arithmetic and no copy: Pool2 writes the 160 values in the
order consumed by FC. The previous GAP path is not part of production. Its
standalone modules remain only as verified generic/legacy IP.

Batch normalization is folded into convolution weights and biases at export.
SAME-padding uses integer zero because activation zero point is zero.

## 3. Integer contract

- Activations and weights: signed INT8.
- Convolution and FC products: signed `INT8×INT8`.
- Biases, partial sums, and raw logits: signed INT32.
- Activations are symmetric per tensor; weights are symmetric per output channel.
- Conv requantization has one signed Q0.31 multiplier and shift per output
  channel and follows TFLite/gemmlowp double rounding.
- Conv/ReLU clamps to `[0,127]`; MaxPool and Flatten preserve scale.
- FC is not requantized. Software applies exported per-class comparison
  multiplier/shift before argmax.

A one-LSB disagreement between exporter, Python golden model, and RTL is a
verification failure.

## 4. Datapath, scheduling, and storage

```text
activation SRAM A/B -> window generator -> shared Matrix Unit -> requant
       |                                      ^
       +------------ MaxPool/Vector Unit      |
weight SRAM ----------------------------------+

Pool2 in bank A (160 INT8, NHWC) -> shared Matrix Unit as 1×1 Conv/FC
                                  -> four INT32 logits
```

Activation flow:

```text
input A(320 B) -> Conv1 B(2560 B) -> Pool1 A(640 B)
 -> Conv2 B(640 B) -> Pool2 A(160 B) -> FC logits
```

The production 8-column weight SRAM contains 256 packed 64-bit words:

| Layer | Word range | Words |
| --- | ---: | ---: |
| Conv1 | `[0,9)` | 9 |
| Conv2 | `[9,81)` | 72 |
| FC | `[81,241)` | 160 |
| Spare | `[241,256)` | 15 |

The training model has 1,324 trainable parameters. Deployment weight values
are 72 + 576 + 640 = 1,288 INT8 values; folded biases and quantization
parameters are stored separately.

The baseline controller keeps one packed partial sum live. The remote tiled
work is preserved through `OPT_GATHER_LOAD`, `OPT_SPATIAL_TILE`, and
`SPATIAL_TILE`: the optimized path applies only to the fixed 4×8 Conv2
descriptor and falls back for other descriptors. K-major spatial tiling keeps
one INT32 partial sum per selected output position so a loaded weight group can
serve several positions. Tile16 uses 512 B for partial sums plus tags and
activation packs. These behavioral capacities are not final physical SRAM,
area, timing, or power results.

## 5. Control and profiler

`tinycnn8_npu_top` sequences Conv1, Pool1, Conv2, Pool2, and FC. Conv1, Conv2,
and FC share one `conv2d_engine`/Matrix Unit; both pools share one
`maxpool2x2_engine`/Vector Unit. The core remains bus-independent.

The six profiler lanes remain ABI-stable: Conv1, Pool1, Conv2, Pool2,
reserved, FC. Lane 4 was GAP and is now always zero. Counters track total and
per-stage cycles, weight-row handshakes, matrix issues/retires, peak in-flight
transactions, validity, and saturation overflow.

The reusable RTL interface accepts `class_count=1..8`; the frozen trained
deployment package has exactly four classes and 160 FC input features. A model
change requires matching weights, parameters, labels, and regression vectors.

## 6. Verification gates

1. Unit regressions for PE, Matrix Unit, Vector Unit, requant, pooling, address
   generation, baseline and tiled Conv2 scheduling.
2. Synthetic full-network tests on 4×8 and 4×4 arrays.
3. MMIO protocol, profiler, and error-path regression.
4. Full test-set Python integer accuracy evaluation.
5. At least 100 real speech samples comparing all four RTL raw logits
   bit-for-bit against the deployed Python integer model.

Icarus Verilog is the reproducible NPU simulator. Commercial simulation may
supplement but does not replace it.
