#!/usr/bin/env python3
"""Independent, standard-library-only integer oracle and TinyCNN-8 fixtures.

Activations are flat signed NHWC arrays. Convolution weights are canonical
W[oc][ky][kx][ic], not an emulation of the Matrix Unit's four-row schedule.
Hex files use two's-complement values and packed weights put lane 0 in bits 7:0.
All generated models are synthetic test data, not trained classifiers.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random


INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
LANES = 8
WEIGHT_WORDS = 256
WEIGHT_BASES = {"conv1": 0, "conv2": 32, "fc": 192}
GAP_MULTIPLIER = 0x66666666
GAP_SHIFT = -4
PATTERNS = ("random", "impulse", "extreme")


def wrap32(value):
    """Interpret the low 32 bits as a signed INT32 (including encoded inputs)."""
    value = int(value) & 0xFFFFFFFF
    return value - (1 << 32) if value >= (1 << 31) else value


def saturating_left_shift(value, amount):
    """Saturate before multiplying by Q31, rather than wrapping the left shift."""
    if not 0 <= amount <= 31:
        raise ValueError("left shift must be in 0..31")
    wide = wrap32(value) * (1 << amount)
    return min(INT32_MAX, max(INT32_MIN, wide))


def saturating_rounding_doubling_high_mul(a, b):
    """TFLite SRDHM: round to nearest, with exact halves toward positive infinity.

    In particular, (-1 * 0x40000000) rounds to zero, not minus one. Computing
    a mathematical floor quotient and remainder avoids copying RTL nudges or
    relying on Python's ties-to-even round() or negative truncating division.
    """
    a, b = wrap32(a), wrap32(b)
    if a == INT32_MIN and b == INT32_MIN:
        return INT32_MAX
    divisor = 1 << 31
    quotient, remainder = divmod(a * b, divisor)
    return quotient + int(2 * remainder >= divisor)


def rounding_divide_by_pot(value, exponent):
    """Round INT32 / 2**exponent to nearest, with exact halves away from zero."""
    if not 0 <= exponent <= 31:
        raise ValueError("right shift must be in 0..31")
    value = wrap32(value)
    divisor = 1 << exponent
    quotient, remainder = divmod(abs(value), divisor)
    rounded = quotient + int(2 * remainder >= divisor)
    return -rounded if value < 0 else rounded


def multiply_by_quantized_multiplier(value, multiplier, shift):
    """Official double-rounding Q31 contract; encoded -32 is reserved."""
    if not -31 <= shift <= 31:
        raise ValueError("shift must be in -31..31")
    shifted = saturating_left_shift(value, max(shift, 0))
    high = saturating_rounding_doubling_high_mul(shifted, multiplier)
    return rounding_divide_by_pot(high, max(-shift, 0))


def requant(acc, bias, multiplier, shift, output_offset=0,
            activation_min=-128, activation_max=127):
    """Wrap bias once, scale, add a signed offset in 33 bits, then clamp to INT8.

    Arguments representing INT32 values may be signed integers or unsigned
    two's-complement encodings. The offset addition is deliberately NOT wrap32.
    """
    if not -128 <= activation_min <= activation_max <= 127:
        raise ValueError("activation bounds must be ordered signed INT8 values")
    biased = wrap32(wrap32(acc) + wrap32(bias))
    scaled = multiply_by_quantized_multiplier(biased, multiplier, shift)
    with_offset = scaled + wrap32(output_offset)
    return min(activation_max, max(activation_min, with_offset))


def _check_tensor(values, height, width, channels):
    if min(height, width, channels) <= 0:
        raise ValueError("tensor dimensions must be positive")
    if len(values) != height * width * channels:
        raise ValueError("tensor length does not match NHWC shape")
    if any(not -128 <= value <= 127 for value in values):
        raise ValueError("tensor values must be signed INT8")


def conv2d_same(values, height, width, input_channels,
                weights, bias, multiplier, shift):
    """Stride-one odd-kernel SAME convolution with one bias and fused ReLU."""
    _check_tensor(values, height, width, input_channels)
    output_channels = len(weights)
    if not output_channels or any(len(p) != output_channels
                                  for p in (bias, multiplier, shift)):
        raise ValueError("one parameter is required per output channel")
    kernel_height, kernel_width = len(weights[0]), len(weights[0][0])
    if kernel_height % 2 != 1 or kernel_width % 2 != 1:
        raise ValueError("SAME convolution requires nonempty odd kernels")
    for channel in weights:
        if len(channel) != kernel_height:
            raise ValueError("inconsistent kernel height")
        for row in channel:
            if len(row) != kernel_width:
                raise ValueError("inconsistent kernel width")
            for tap in row:
                if len(tap) != input_channels or any(not -128 <= w <= 127 for w in tap):
                    raise ValueError("weights must match signed INT8 input channels")

    result = []
    for y in range(height):
        for x in range(width):
            for oc in range(output_channels):
                acc = 0
                for ky in range(kernel_height):
                    iy = y + ky - kernel_height // 2
                    if not 0 <= iy < height:
                        continue  # symmetric quantization pads with real zero
                    for kx in range(kernel_width):
                        ix = x + kx - kernel_width // 2
                        if not 0 <= ix < width:
                            continue
                        source = (iy * width + ix) * input_channels
                        for ic in range(input_channels):
                            acc = wrap32(acc + values[source + ic] * weights[oc][ky][kx][ic])
                result.append(requant(acc, bias[oc], multiplier[oc], shift[oc],
                                      activation_min=0))
    return result


def maxpool2x2(values, height, width, channels):
    """Signed NHWC maximum over nonoverlapping 2x2 windows."""
    _check_tensor(values, height, width, channels)
    if height % 2 or width % 2:
        raise ValueError("2x2 pooling requires even spatial dimensions")
    result = []
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            for channel in range(channels):
                result.append(max(values[((y + dy) * width + x + dx) * channels + channel]
                                  for dy in range(2) for dx in range(2)))
    return result


def global_average_pool(values, height, width, channels, multiplier, shift):
    """Wrap spatial sums, then requantize once; do not integer-divide first."""
    _check_tensor(values, height, width, channels)
    if len(multiplier) != channels or len(shift) != channels:
        raise ValueError("one GAP parameter is required per channel")
    sums = [0] * channels
    for position in range(height * width):
        for channel in range(channels):
            sums[channel] = wrap32(sums[channel] + values[position * channels + channel])
    return [requant(sums[c], 0, multiplier[c], shift[c]) for c in range(channels)]


def fully_connected(values, weights, bias):
    """Canonical W[oc][ic] dot products and one INT32 bias; no requantization."""
    if len(weights) != len(bias) or any(len(row) != len(values) for row in weights):
        raise ValueError("FC weights and bias must match the input/output dimensions")
    if any(not -128 <= value <= 127 for value in values):
        raise ValueError("FC activations must be signed INT8")
    if any(not -128 <= weight <= 127 for row in weights for weight in row):
        raise ValueError("FC weights must be signed INT8")
    result = []
    for oc, row in enumerate(weights):
        acc = 0
        for ic, weight in enumerate(row):
            acc = wrap32(acc + values[ic] * weight)
        result.append(wrap32(acc + wrap32(bias[oc])))
    return result


def _random_conv(rng, input_channels, tap_limit):
    return [[[[rng.randint(-tap_limit, tap_limit) for _ in range(input_channels)]
              for _ in range(3)] for _ in range(3)] for _ in range(LANES)]


def _zero_channel(input_channels):
    return [[[0] * input_channels for _ in range(3)] for _ in range(3)]


def build_model(seed=7, classes=6, pattern="random"):
    """Build reproducible small-integer synthetic parameters in canonical order.

    Two sparse witness channels ensure useful nonsaturated signals for every
    supported pattern. The remaining channels retain signed random 3x3 taps.
    Baseline class counts are four and six; other hardware-valid counts work too.
    """
    if not 1 <= classes <= LANES:
        raise ValueError("classes must be in 1..8")
    if pattern not in PATTERNS:
        raise ValueError("pattern must be random, impulse, or extreme")
    rng = random.Random(seed)
    # Consume the same input RNG draws for all patterns, keeping model weights
    # identical when comparing random, impulse, and extreme inputs at one seed.
    values = [rng.randint(-16, 16) for _ in range(20 * 16)]
    if pattern == "impulse":
        values = [0] * (20 * 16)
        values[0] = -128
        values[10 * 16 + 8] = 127
        values[19 * 16 + 15] = 63
    elif pattern == "extreme":
        values = [-128 if (y + x) % 2 == 0 else 127
                  for y in range(20) for x in range(16)]

    conv1_weights = _random_conv(rng, 1, 2)
    conv1_bias = [rng.randint(-8, 16) for _ in range(LANES)]
    conv1_weights[0] = _zero_channel(1)
    conv1_weights[0][1][1][0] = 1
    conv1_weights[1] = _zero_channel(1)
    conv1_weights[1][1][1][0] = -1
    conv1_bias[:2] = [8, 12]
    conv1_multiplier = [0x60000000, 0x50000000, 0x70000000, 0x48000000,
                        0x64000000, 0x58000000, 0x74000000, 0x44000000]
    conv1_shift = [0, -1, -2, -1, -2, -1, -2, -1]

    conv2_weights = _random_conv(rng, 8, 1)
    conv2_bias = [rng.randint(-12, 20) for _ in range(LANES)]
    conv2_weights[0] = _zero_channel(8)
    conv2_weights[0][1][1][0] = 1
    conv2_weights[1] = _zero_channel(8)
    conv2_weights[1][1][1][1] = 2
    conv2_bias[:2] = [2, 64]
    conv2_multiplier = [0x60000000, 0x50000000, 0x68000000, 0x54000000,
                        0x70000000, 0x48000000, 0x5C000000, 0x74000000]
    conv2_shift = [-1, -2, -3, -3, -4, -3, -4, -3]

    fc_weights = [[rng.randint(-3, 3) for _ in range(LANES)] for _ in range(classes)]
    fc_bias = [(oc - 2) * 17 + rng.randint(-16, 16) for oc in range(classes)]
    fc_weights[0] = [1] + [0] * 7
    fc_bias[0] = -17
    if classes > 1:
        fc_weights[1] = [0, 1] + [0] * 6
        fc_bias[1] = 23

    return {
        "seed": seed, "classes": classes, "pattern": pattern, "input": values,
        "conv1": {"weights": conv1_weights, "bias": conv1_bias,
                  "multiplier": conv1_multiplier, "shift": conv1_shift},
        "conv2": {"weights": conv2_weights, "bias": conv2_bias,
                  "multiplier": conv2_multiplier, "shift": conv2_shift},
        "gap": {"multiplier": [GAP_MULTIPLIER] * LANES, "shift": [GAP_SHIFT] * LANES},
        "fc": {"weights": fc_weights, "bias": fc_bias},
    }


def run_model(model):
    """Evaluate all six stages from canonical weights, independently of packing."""
    c1 = conv2d_same(model["input"], 20, 16, 1, **model["conv1"])
    p1 = maxpool2x2(c1, 20, 16, 8)
    c2 = conv2d_same(p1, 10, 8, 8, **model["conv2"])
    p2 = maxpool2x2(c2, 10, 8, 8)
    gap = global_average_pool(p2, 5, 4, 8, **model["gap"])
    fc = fully_connected(gap, **model["fc"])
    return {"conv1": c1, "pool1": p1, "conv2": c2, "pool2": p2, "gap": gap, "fc": fc}


def pack_lanes(values, bits=8, lanes=LANES):
    """Pack low bits of each lane, lane zero least significant; pad with zeros."""
    if bits <= 0 or lanes <= 0 or len(values) > lanes:
        raise ValueError("invalid packed-word dimensions")
    mask = (1 << bits) - 1
    return sum((value & mask) << (lane * bits) for lane, value in enumerate(values))


def pack_weights(model):
    """Pack each canonical k into one 8-lane word; bases are WORD addresses."""
    words = [0] * WEIGHT_WORDS
    for name in ("conv1", "conv2"):
        weights = model[name]["weights"]
        input_channels = len(weights[0][0][0])
        k = 0
        for ky in range(3):
            for kx in range(3):
                for ic in range(input_channels):
                    words[WEIGHT_BASES[name] + k] = pack_lanes(
                        [weights[oc][ky][kx][ic] for oc in range(LANES)])
                    k += 1
    for ic in range(LANES):
        words[WEIGHT_BASES["fc"] + ic] = pack_lanes(
            [row[ic] for row in model["fc"]["weights"]])
    return words


def parameter_arrays(model):
    """Flat parameter[layer * 8 + lane]: C1, C2, GAP, FC (not stage IDs)."""
    bias = model["conv1"]["bias"] + model["conv2"]["bias"] + [0] * LANES
    bias += model["fc"]["bias"] + [0] * (LANES - model["classes"])
    multiplier = (model["conv1"]["multiplier"] + model["conv2"]["multiplier"]
                  + model["gap"]["multiplier"] + [0] * LANES)
    shift = (model["conv1"]["shift"] + model["conv2"]["shift"]
             + model["gap"]["shift"] + [0] * LANES)
    return bias, multiplier, shift


def _hex_file(directory, name, values, bits):
    mask = (1 << bits) - 1
    content = "".join(f"{value & mask:0{bits // 4}x}\n" for value in values).encode("ascii")
    (directory / name).write_bytes(content)
    return {"lines": len(values), "hex_digits": bits // 4, "element_bits": bits,
            "logical_bytes": len(values) * bits // 8,
            "sha256": hashlib.sha256(content).hexdigest()}


def _stats(values):
    return {"min": min(values), "max": max(values), "distinct_values": len(set(values)),
            "nonzero": sum(value != 0 for value in values),
            "equal_127": sum(value == 127 for value in values)}


def _layers(classes):
    c1_nominal, c1_useful = 20 * 16 * 8 * 9, (3 * 20 - 2) * (3 * 16 - 2) * 8
    c2_nominal, c2_useful = 10 * 8 * 8 * 9 * 8, (3 * 10 - 2) * (3 * 8 - 2) * 8 * 8
    return [
        {"id": 0, "name": "conv1", "operation": "conv2d_same_relu", "input_shape": [20, 16, 1],
         "output_shape": [20, 16, 8], "kernel_shape": [3, 3], "stride": [1, 1],
         "padding": "SAME", "parameter_layer": 0, "weight_base_packedword": 0,
         "weight_words": 9, "weight_shape": [8, 3, 3, 1],
         "nominal_macs": c1_nominal, "useful_macs": c1_useful},
        {"id": 1, "name": "pool1", "operation": "maxpool2x2", "input_shape": [20, 16, 8],
         "output_shape": [10, 8, 8], "stride": [2, 2], "nominal_macs": 0, "useful_macs": 0},
        {"id": 2, "name": "conv2", "operation": "conv2d_same_relu", "input_shape": [10, 8, 8],
         "output_shape": [10, 8, 8], "kernel_shape": [3, 3], "stride": [1, 1],
         "padding": "SAME", "parameter_layer": 1, "weight_base_packedword": 32,
         "weight_words": 72, "weight_shape": [8, 3, 3, 8],
         "nominal_macs": c2_nominal, "useful_macs": c2_useful},
        {"id": 3, "name": "pool2", "operation": "maxpool2x2", "input_shape": [10, 8, 8],
         "output_shape": [5, 4, 8], "stride": [2, 2], "nominal_macs": 0, "useful_macs": 0},
        {"id": 4, "name": "gap", "operation": "sum_then_q31", "input_shape": [5, 4, 8],
         "output_shape": [1, 1, 8], "parameter_layer": 2, "reduction_positions": 20,
         "multiplier": GAP_MULTIPLIER, "shift": GAP_SHIFT,
         "nominal_macs": 0, "useful_macs": 0},
        {"id": 5, "name": "fc", "operation": "fully_connected_int32", "input_shape": [1, 1, 8],
         "output_shape": [1, 1, classes], "parameter_layer": 3,
         "weight_base_packedword": 192, "weight_words": 8, "weight_shape": [classes, 8],
         "nominal_macs": classes * 8, "useful_macs": classes * 8},
    ]


def generate(output_dir, seed=7, classes=6, pattern="random"):
    """Write fixed TB-compatible files and return {'manifest': ..., 'golden': ...}.

    The manifest hashes every hex file's exact bytes, including final newlines.
    It intentionally omits timestamps and output paths, so fixtures are stable.
    """
    model = build_model(seed, classes, pattern)
    golden = run_model(model)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    files = {"input.hex": _hex_file(directory, "input.hex", model["input"], 8),
             "weights.hex": _hex_file(directory, "weights.hex", pack_weights(model), 64)}
    for name, values, bits in zip(("bias", "multiplier", "shift"), parameter_arrays(model),
                                 (32, 32, 8)):
        filename = name + ".hex"
        files[filename] = _hex_file(directory, filename, values, bits)
    for name, values in golden.items():
        filename = "golden_" + name + ".hex"
        files[filename] = _hex_file(directory, filename, values, 32 if name == "fc" else 8)
    layers = _layers(classes)
    manifest = {
        "format_version": 1, "synthetic": True, "trained": False,
        "description": "Deterministic synthetic TinyCNN-8 RTL verification fixture",
        "seed": seed, "classes": classes, "pattern": pattern, "tensor_order": "NHWC",
        "array_rows": 4, "array_cols": 8, "input_shape": [20, 16, 1],
        "output_shape": [1, 1, classes], "layers": layers,
        "weight_packing": {"word_bits": 64, "words": WEIGHT_WORDS,
                           "base_address_unit": "packedword", "bases": dict(WEIGHT_BASES),
                           "lane0_bits": [7, 0], "canonical_order": "W[oc][ky][kx][ic]",
                           "word_order": "base + (ky * kernel_width + kx) * input_channels + ic",
                           "unused_words_and_lanes": "zero"},
        "parameter_layout": {"index": "layer * 8 + lane", "lanes": LANES,
                             "layers": {"conv1": 0, "conv2": 1, "gap": 2, "fc": 3},
                             "base_address_unit": "scalar_parameter", "shift_storage_bits": 8,
                             "shift_signed": True, "rtl_shift_bits": 6,
                             "gap_bias": "ignored; zero", "fc_multiplier_and_shift": "ignored; zero"},
        "integer_contract": {"accumulator": "INT32 wrap after each product/add and bias once",
                             "valid_shift_range": [-31, 31], "reserved_shift": -32,
                             "q31_high_mul_ties": "toward positive infinity",
                             "q31_right_shift_ties": "away from zero",
                             "positive_shift": "saturating INT32 before Q31 high multiply",
                             "offset": "sign-extend INT32 operands and add in 33 bits before clamp",
                             "conv_clamp": [0, 127], "gap_clamp": [-128, 127],
                             "gap": "sum all 20 positions, then Q31; never integer-divide first",
                             "fc": "INT32 logits without requantization"},
        "macs": {"nominal": sum(layer["nominal_macs"] for layer in layers),
                 "useful": sum(layer["useful_macs"] for layer in layers),
                 "useful_definition": "In-bounds products only; excludes SAME padding, not numeric zeros"},
        "statistics": {name: _stats(values) for name, values in golden.items()},
        "files": files,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                               encoding="utf-8")
    return {"manifest": manifest, "golden": golden}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="directory for hex files and manifest")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--classes", type=int, choices=range(1, 9), default=6)
    parser.add_argument("--pattern", choices=PATTERNS, default="random")
    args = parser.parse_args(argv)
    result = generate(args.output, args.seed, args.classes, args.pattern)
    print(json.dumps({"output": str(args.output), "seed": args.seed, "classes": args.classes,
                      "pattern": args.pattern, "synthetic": True,
                      "gap": result["golden"]["gap"], "fc": result["golden"]["fc"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
