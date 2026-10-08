# KWS-TinyCNN-8 NPU Architecture Baseline

Status: compute-core baseline. The authoritative SoC integration and
software/hardware boundary are documented in `docs/ARCHITECTURE.md`.

This document freezes the model-visible behavior of the first NPU version. The
MAC array dimensions remain parameters until synthesis results are available.

## 1. Accelerator boundary

The CPU or host performs audio sampling and MFCC/log-mel feature extraction.
The NPU accepts one signed INT8 `20 x 16 x 1` feature tensor and returns up to
eight signed INT32 logits. Softmax, thresholding, and argmax are CPU software
responsibilities.

Tensor storage uses NHWC order. Channel is the fastest-changing dimension and
channels are handled in groups of up to eight lanes.

## 2. Baseline network

| Stage | Operation | Output shape |
| --- | --- | --- |
| Input | signed INT8 feature tensor | `20 x 16 x 1` |
| 1 | Conv2D `1 -> 8`, `3 x 3`, stride 1, SAME | `20 x 16 x 8` |
| 2 | fused bias, requantization, ReLU | `20 x 16 x 8` |
| 3 | MaxPool `2 x 2`, stride 2 | `10 x 8 x 8` |
| 4 | Conv2D `8 -> 8`, `3 x 3`, stride 1, SAME | `10 x 8 x 8` |
| 5 | fused bias, requantization, ReLU | `10 x 8 x 8` |
| 6 | MaxPool `2 x 2`, stride 2 | `5 x 4 x 8` |
| 7 | Global sum/average pool over 20 positions | `1 x 1 x 8` |
| 8 | Fully connected `8 -> 4/6`, hardware maximum 8 | `1 x 1 x 4/6` |
| Output | signed INT32 logits | 4 or 6 valid lanes |

Batch normalization is folded into convolution weights and biases before model
export. Inference hardware does not contain a batch-normalization unit.

For SAME padding, a location outside the input tensor has the quantized value
of real zero. The baseline symmetric activation format therefore pads with the
signed integer value zero.

## 3. Integer contract

- Activations and weights are signed INT8.
- Convolution and fully-connected products are signed `INT8 x INT8`.
- Bias and partial sums are signed INT32.
- Weight quantization is symmetric and per output channel.
- Activation quantization is symmetric and per tensor for the first version.
- Each requantized output channel has a signed Q0.31 multiplier and a signed
  power-of-two shift.
- Requantization uses the TFLite/CMSIS-NN style sequence: saturating rounding
  doubling-high multiply, followed by rounding divide by a power of two.
- ReLU is fused by clamping the requantized result to `[0, 127]`. ReLU may be
  disabled, in which case the clamp interval is `[-128, 127]`.
- MaxPool preserves the input scale.
- Global pooling accumulates in INT32, then uses the Requant Unit to convert
  the sum back to signed INT8 for the existing Matrix Unit FC input. Its
  multiplier/shift combines division by 20 with the FC input scale.
- The final FC result remains INT32. Because FC weights are per-output-channel,
  CPU software must rescale the valid logits to a common comparison scale
  before argmax, using per-class parameters supplied by the model package.

The exporter, Python golden model, and RTL testbench must use the same rounding
and saturation functions. A mismatch of one least-significant bit is a test
failure.

## 4. Datapath and storage

```text
 host/CPU
    |
    v
 activation SRAM A/B -- window/address generator -- matrix unit
                                                   |
 weight SRAM --------------------------------------+
                                                   v
                                      INT32 bias / partial sums
                                                   |
                                                   v
                                 requant + optional ReLU clamp
                                                   |
                                                   v
                                      max-pool / activation SRAM
                                                   |
                                                   v
                                           INT32 GAP accumulator
                                                   |
                                                   v
                                             matrix unit (FC)
                                                   |
                                                   v
                                             INT32 logits
```

Two activation banks are used in ping-pong fashion. Each bank must hold at
least the largest INT8 feature map, `20 x 16 x 8 = 2560` bytes. The logical
architecture uses writable SRAM interfaces; simulation initially uses
behavioral arrays and physical design may replace them with foundry SRAM
macros.

The four/six-class model contains 680/696 INT8 weights and 20/22 INT32 biases:
700/718 scalar parameters, not bytes. Raw weights plus biases occupy 760/784
bytes; Q31 multipliers and shifts require additional storage. The behavioral
4x8 top allocates 256 packed weight words (2 KiB), separate parameter arrays,
and two 4 KiB activation banks. This is not the SoC's 8 KiB main-SRAM budget.

The existing weight-stationary Matrix Unit is retained. The default controller
keeps one packed partial sum live. Optional K-major spatial tiling accelerates
only the fixed 4x8 Conv2 descriptor; other descriptors retain the baseline
schedule. Tile16 uses 512 B partial sums, 64 B integer tags and 8 B activation
packs, with additional control/profiler logic. Array shape and tile size remain
parameters, not a synthesized ASIC selection. The current experiment compares
4x4 fallback and 4x8 schedules; no claim is made that 1x8/2x8 have been accepted
by the new tiled regression.

## 5. Control boundary

The NPU core is independent of the SoC bus. The production SoC adapts it through
`hardware/soc/rtl/npu/tinycnn8_npu_mmio_wrapper.sv`; the complete register map
and loading protocol are specified in `docs/ARCHITECTURE.md`.

Execution engines are configured by compact layer-descriptor fields rather
than a general instruction set. In the first TinyCNN-8 integration,
`tinycnn8_npu_top` emits the fixed model's descriptors as a small combinational
microcode table selected by its sequencer; a later SoC wrapper may replace this
table with writable descriptors without changing the compute engines. A
descriptor provides at least:

- operation kind;
- input and output base addresses;
- height, width, input channels, and output channels;
- kernel size, stride, and SAME/VALID padding mode;
- weight, bias, multiplier, and shift base addresses;
- activation clamp bounds;
- valid output-lane count.

The first implementation only needs to accept descriptor combinations used by
the baseline network. Unsupported combinations must be rejected or documented;
they must not silently produce a result.

The fixed top accepts `class_count` values from 1 through 8 and refuses zero or
larger values without starting a job. A new inference must reload the complete
input feature tensor because activation bank A is reused by later pooling. FC
weights are packed with `ceil(class_count / ARRAY_COLS)` output tiles per input
channel; changing to a model with a different class count therefore requires
reloading its matching FC weights and parameters.

## 6. Verification gates

1. Unit-level randomized tests for PE, Matrix Unit, Vector Unit, requantization,
   pooling, address generation, and memories.
2. Bit-exact single-layer comparison against an independent software model.
3. Full-network comparison using deterministic synthetic weights and features.
4. Full-network comparison using trained and folded INT8 parameters.
5. Layer-by-layer dumps for at least 100 real test samples, followed by a
   larger classification regression.

Icarus Verilog remains the required open-source RTL simulator for the baseline
regression. Additional Verilator or commercial-simulator runs may be added but
must not replace the reproducible Icarus test.
