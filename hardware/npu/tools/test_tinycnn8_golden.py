#!/usr/bin/env python3
"""Hand-derived integer, packing, and fixture tests; no RTL or third-party deps."""

import hashlib
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction


MODULE_PATH = Path(__file__).with_name("tinycnn8_golden.py")
SPEC = importlib.util.spec_from_file_location("tinycnn8_golden", MODULE_PATH)
golden = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(golden)
MIN32, MAX32 = -(1 << 31), (1 << 31) - 1
HALF, QUARTER = 0x40000000, 0x20000000


class IntegerContractTests(unittest.TestCase):
    def test_wrap32_hand_values(self):
        cases = [(0, 0), (MAX32, MAX32), (MIN32, MIN32), (MAX32 + 1, MIN32),
                 (MIN32 - 1, MAX32), (0xFFFFFFFF, -1), (1 << 32, 0),
                 (-(1 << 32) - 3, -3), ((1 << 80) + 0x80000001, MIN32 + 1)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(golden.wrap32(value), expected)

    def test_high_mul_halves_including_negative(self):
        # High multiply is NOT ties-to-even and NOT ties-away-from-zero.
        for value, expected in [(-5, -2), (-3, -1), (-1, 0), (0, 0),
                                (1, 1), (3, 2), (5, 3)]:
            with self.subTest(value=value):
                self.assertEqual(golden.saturating_rounding_doubling_high_mul(value, HALF), expected)
        self.assertEqual(golden.saturating_rounding_doubling_high_mul(3, -HALF), -1)
        self.assertEqual(golden.saturating_rounding_doubling_high_mul(-3, -HALF), 2)

    def test_high_mul_special_point_and_extremes(self):
        cases = [(MIN32, MIN32, MAX32), (MIN32, MAX32, -MAX32),
                 (MAX32, MIN32, -MAX32), (MAX32, MAX32, MAX32 - 1),
                 (MIN32, HALF, -(1 << 30)), (-1, -1, 0)]
        for a, b, expected in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(golden.saturating_rounding_doubling_high_mul(a, b), expected)

    def test_high_mul_exact_rational_rounding_interval(self):
        # Algebraic nearest-rounding invariant, independent of RTL's nudge/divide.
        rng = random.Random(91)
        pairs = [(a, b) for a in range(-9, 10)
                 for b in (QUARTER, HALF, -QUARTER, -HALF, MIN32, MAX32)]
        pairs += [(rng.randint(MIN32, MAX32), rng.randint(MIN32, MAX32)) for _ in range(200)]
        for a, b in pairs:
            rounded = golden.saturating_rounding_doubling_high_mul(a, b)
            exact = Fraction(a * b, 1 << 31)
            self.assertLessEqual(Fraction(rounded) - Fraction(1, 2), exact)
            self.assertLess(exact, Fraction(rounded) + Fraction(1, 2))

    def test_right_division_halves_away_from_zero(self):
        for value, expected in [(-7, -4), (-5, -3), (-3, -2), (-1, -1),
                                (0, 0), (1, 1), (3, 2), (5, 3), (7, 4)]:
            with self.subTest(value=value):
                self.assertEqual(golden.rounding_divide_by_pot(value, 1), expected)
        self.assertEqual(golden.rounding_divide_by_pot(-6, 2), -2)
        self.assertEqual(golden.rounding_divide_by_pot(-5, 2), -1)
        self.assertEqual(golden.rounding_divide_by_pot(MIN32, 0), MIN32)
        self.assertEqual(golden.rounding_divide_by_pot(MIN32, 31), -1)
        with self.assertRaises(ValueError):
            golden.rounding_divide_by_pot(MIN32, 32)

    def test_double_rounding_not_single_rounding(self):
        # 1 * .5 -> 1, then /2 -> 1, although round(.25) would be zero.
        self.assertEqual(golden.multiply_by_quantized_multiplier(1, HALF, -1), 1)
        # -3 * .25 -> -1, then /2 -> -1, although round(-.375) is zero.
        self.assertEqual(golden.multiply_by_quantized_multiplier(-3, QUARTER, -1), -1)
        self.assertEqual(golden.multiply_by_quantized_multiplier(-1, HALF, -1), 0)

    def test_pre_left_shift_saturates_and_signed_shift_extremes(self):
        self.assertEqual(golden.saturating_left_shift(1 << 30, 1), MAX32)
        self.assertEqual(golden.saturating_left_shift(-(1 << 30) - 1, 1), MIN32)
        self.assertEqual(golden.saturating_left_shift(-1, 31), MIN32)
        self.assertEqual(golden.saturating_left_shift(0, 31), 0)
        # A tiny multiplier keeps the saturation decision visible before INT8 clamp.
        self.assertEqual(golden.requant(1 << 30, 0, 1, 1), 1)
        self.assertEqual(golden.requant(-(1 << 30) - 1, 0, 1, 1), -1)
        self.assertEqual(golden.requant(2, 0, 1, 31), 1)
        self.assertEqual(golden.requant(-2, 0, 1, 31), -1)
        self.assertEqual(golden.requant(MIN32, 0, MIN32, -31), 1)
        self.assertEqual(golden.requant(MIN32, 0, MAX32, -31), -1)

    def test_bias_wrap_and_int8_clamps(self):
        self.assertEqual(golden.requant(MAX32, 1, HALF, 0), -128)
        self.assertEqual(golden.requant(MIN32, -1, HALF, 0), 127)
        self.assertEqual(golden.requant(254, 0, HALF, 0), 127)
        self.assertEqual(golden.requant(256, 0, HALF, 0), 127)
        self.assertEqual(golden.requant(-256, 0, HALF, 0), -128)
        self.assertEqual(golden.requant(-258, 0, HALF, 0), -128)
        self.assertEqual(golden.requant(-4, 0, HALF, 0, activation_min=0), 0)
        self.assertEqual(golden.requant(40, 0, HALF, 0, activation_min=-5, activation_max=7), 7)

    def test_33bit_offset_and_signed_multiplier_encoding(self):
        # Wrapping the offset sum to 32 bits would incorrectly produce -3 and +1.
        self.assertEqual(golden.requant(MAX32, 0, MAX32, 0, output_offset=MAX32), 127)
        self.assertEqual(golden.requant(MIN32, 0, MAX32, 0, output_offset=MIN32), -128)
        self.assertEqual(golden.requant(MIN32, 0, MIN32, 0, output_offset=MIN32), -1)
        self.assertEqual(golden.requant(MIN32, 0, MIN32, 0, output_offset=-MAX32), 0)
        self.assertEqual(golden.requant(0x80000000, 0, 0x80000000, 0,
                                       output_offset=0x80000000), -1)
        self.assertEqual(golden.requant(0, 0, 0, 0, output_offset=0xFFFFFFFF), -1)
        self.assertEqual(golden.requant(3, 0, 0xC0000000, 0), -1)

    def test_reject_invalid_shift_and_clamp(self):
        for shift in (-33, -32, 32):
            with self.assertRaises(ValueError):
                golden.requant(1, 0, HALF, shift)
        for bounds in ((-129, 127), (-128, 128), (7, 6)):
            with self.assertRaises(ValueError):
                golden.requant(1, 0, HALF, 0, activation_min=bounds[0], activation_max=bounds[1])


class LayerAndPackingTests(unittest.TestCase):
    def test_conv_same_edges_and_bias_once(self):
        weights = [[[[1] for _ in range(3)] for _ in range(3)]]
        # Valid contributions are 4/6/9 taps; bias 6 is added only once.
        self.assertEqual(golden.conv2d_same([2] * 9, 3, 3, 1, weights, [6], [HALF], [0]),
                         [7, 9, 7, 9, 12, 9, 7, 9, 7])
        directional = [[[[0] for _ in range(3)] for _ in range(3)]]
        directional[0][0][0][0] = 2
        self.assertEqual(golden.conv2d_same([1, 2, 3, 4], 2, 2, 1,
                                           directional, [0], [HALF], [0]), [0, 0, 0, 1])

    def test_conv_canonical_channel_order_nhwc(self):
        weights = [[[[2, 0]]], [[[2, 4]]]]
        self.assertEqual(golden.conv2d_same([1, 10, 2, 20, 3, 30, 4, 40], 2, 2, 2,
                                           weights, [0, 0], [HALF, HALF], [0, 0]),
                         [1, 21, 2, 42, 3, 63, 4, 84])
        self.assertEqual(golden.conv2d_same([1], 1, 1, 1, [[[[2]]]],
                                           [MAX32], [HALF], [0]), [0])

    def test_maxpool_signed_values_and_spatial_order(self):
        self.assertEqual(golden.maxpool2x2([-128, 10, -7, -5, 20, 30, 3, 127], 2, 2, 2),
                         [20, 127])
        self.assertEqual(golden.maxpool2x2([1, 2, 3, 4, 8, 7, 6, 5], 2, 4, 1), [8, 6])

    def test_gap_sum_q31_not_early_integer_average(self):
        # Sum 10 -> high product 8 -> /16 half -> 1; early //20 would lose it.
        values = [10, 30, -10] + [0] * (19 * 3)
        self.assertEqual(golden.global_average_pool(values, 5, 4, 3,
                                                    [0x66666666] * 3, [-4] * 3), [1, 2, -1])
        self.assertEqual(golden.global_average_pool([127, -128] * 20, 5, 4, 2,
                                                    [0x66666666] * 2, [-4] * 2), [127, -128])

    def test_fc_signed_products_bias_once_no_int8_clamp(self):
        self.assertEqual(golden.fully_connected([-2, 3], [[4, -5], [-3, 2]], [7, -20]), [-16, -8])
        self.assertEqual(golden.fully_connected([-128, 127], [[-128, 127]], [MAX32]),
                         [MIN32 + 32512])
        self.assertEqual(golden.fully_connected([127], [[127]], [0]), [16129])

    def test_fc_accumulator_wrap_without_bias_overflow(self):
        # 131072 products of (-128)*(-128) equal 2**31 exactly.
        count = 131073
        self.assertEqual(golden.fully_connected([-128] * count, [[-128] * count], [-16384]), [MIN32])

    def test_packed_lane_endianness_and_high_bits(self):
        self.assertEqual(golden.pack_lanes([-128, -1, 0, 1, 127, -2, 2, -127]),
                         0x8102FE7F0100FF80)
        self.assertEqual(golden.pack_lanes([-1, MIN32], bits=32), 0x80000000FFFFFFFF)
        self.assertEqual(golden.pack_lanes([1, 2]), 0x0201)
        with self.assertRaises(ValueError):
            golden.pack_lanes([0] * 9)

    def test_weight_packedword_bases_and_canonical_k_order(self):
        c1 = [[[[0] for _ in range(3)] for _ in range(3)] for _ in range(8)]
        c2 = [[[[0] * 8 for _ in range(3)] for _ in range(3)] for _ in range(8)]
        fc = [[0] * 8 for _ in range(4)]
        c1[0][0][0][0], c1[7][2][2][0] = -128, 127
        c2[3][1][2][5], c2[0][2][2][7] = -2, 3
        fc[0][0], fc[3][7] = 2, -1
        model = {"conv1": {"weights": c1}, "conv2": {"weights": c2}, "fc": {"weights": fc}}
        words = golden.pack_weights(model)
        expected = {0: 0x80, 8: 0x7F00000000000000, 77: 0xFE000000,
                    103: 3, 192: 2, 199: 0xFF000000}
        self.assertEqual(len(words), 256)
        self.assertEqual({i: word for i, word in enumerate(words) if word}, expected)

    def test_parameter_layer_lane_layout(self):
        model = golden.build_model(classes=4)
        bias, mult, shifts = golden.parameter_arrays(model)
        self.assertEqual([len(values) for values in (bias, mult, shifts)], [32, 32, 32])
        self.assertEqual(bias[:8], model["conv1"]["bias"])
        self.assertEqual(bias[8:16], model["conv2"]["bias"])
        self.assertEqual(bias[16:24], [0] * 8)
        self.assertEqual(bias[24:28], model["fc"]["bias"])
        self.assertEqual(bias[28:], [0] * 4)
        self.assertEqual(mult[16:24], [0x66666666] * 8)
        self.assertEqual(shifts[16:24], [-4] * 8)
        self.assertEqual(mult[24:], [0] * 8)
        self.assertEqual(shifts[24:], [0] * 8)


class GenerationTests(unittest.TestCase):
    def test_impulses_cover_center_and_both_corners(self):
        model = golden.build_model(pattern="impulse")
        self.assertEqual({i: value for i, value in enumerate(model["input"]) if value},
                         {0: -128, 168: 127, 319: 63})

    def test_patterns_change_inputs_not_model_parameters(self):
        models = [golden.build_model(pattern=pattern) for pattern in golden.PATTERNS]
        for model in models[1:]:
            for layer in ("conv1", "conv2", "gap", "fc"):
                self.assertEqual(model[layer], models[0][layer])
        self.assertEqual(set(models[2]["input"]), {-128, 127})
        self.assertNotEqual(models[0]["input"], models[1]["input"])

    def test_all_patterns_classes_lengths_hashes_and_manifest(self):
        shapes = [[20, 16, 8], [10, 8, 8], [10, 8, 8], [5, 4, 8], [1, 1, 8]]
        for pattern in golden.PATTERNS:
            for classes in (4, 6):
                with self.subTest(pattern=pattern, classes=classes), tempfile.TemporaryDirectory() as tmp:
                    result = golden.generate(tmp, classes=classes, pattern=pattern)
                    manifest = json.loads((Path(tmp) / "manifest.json").read_text())
                    self.assertEqual(manifest, result["manifest"])
                    expected = {"input.hex": (320, 2), "weights.hex": (256, 16),
                                "bias.hex": (32, 8), "multiplier.hex": (32, 8), "shift.hex": (32, 2),
                                "golden_conv1.hex": (2560, 2), "golden_pool1.hex": (640, 2),
                                "golden_conv2.hex": (640, 2), "golden_pool2.hex": (160, 2),
                                "golden_gap.hex": (8, 2), "golden_fc.hex": (classes, 8)}
                    self.assertEqual({p.name for p in Path(tmp).iterdir()}, set(expected) | {"manifest.json"})
                    self.assertEqual(set(manifest["files"]), set(expected))
                    for filename, (count, digits) in expected.items():
                        data = (Path(tmp) / filename).read_bytes()
                        lines = data.decode("ascii").splitlines()
                        self.assertEqual(len(lines), count)
                        self.assertTrue(data.endswith(b"\n"))
                        for line in lines:
                            self.assertRegex(line, "^[0-9a-f]{" + str(digits) + "}$")
                        metadata = manifest["files"][filename]
                        self.assertEqual(metadata["lines"], count)
                        self.assertEqual(metadata["hex_digits"], digits)
                        self.assertEqual(metadata["sha256"], hashlib.sha256(data).hexdigest())
                    for name, values in result["golden"].items():
                        bits = 32 if name == "fc" else 8
                        encoded = [int(line, 16) for line in (Path(tmp) / ("golden_" + name + ".hex")).read_text().splitlines()]
                        decoded = [value - (1 << bits) if value >= (1 << (bits - 1)) else value for value in encoded]
                        self.assertEqual(decoded, values)
                    self.assertEqual((Path(tmp) / "shift.hex").read_text().splitlines()[16:24], ["fc"] * 8)
                    self.assertTrue(manifest["synthetic"])
                    self.assertFalse(manifest["trained"])
                    self.assertEqual((manifest["seed"], manifest["classes"], manifest["pattern"]), (7, classes, pattern))
                    self.assertEqual([layer["id"] for layer in manifest["layers"]], list(range(6)))
                    self.assertEqual([layer["output_shape"] for layer in manifest["layers"]], shapes + [[1, 1, classes]])
                    self.assertEqual(manifest["weight_packing"]["bases"], {"conv1": 0, "conv2": 32, "fc": 192})
                    self.assertEqual(manifest["macs"]["nominal"], 69120 + 8 * classes)
                    self.assertEqual(manifest["macs"]["useful"], 60768 + 8 * classes)

    def test_non_degenerate_across_seeds_and_patterns(self):
        for seed in (0, 7, 19, 12345):
            for pattern in golden.PATTERNS:
                with self.subTest(seed=seed, pattern=pattern):
                    outputs = golden.run_model(golden.build_model(seed=seed, pattern=pattern))
                    for layer in ("conv1", "conv2"):
                        self.assertTrue(any(value != 0 for value in outputs[layer]))
                        self.assertTrue(any(0 < value < 127 for value in outputs[layer]))
                        self.assertGreater(len(set(outputs[layer])), 1)
                    self.assertTrue(any(outputs["gap"]))
                    self.assertGreater(len(set(outputs["gap"])), 1)
                    self.assertTrue(any(outputs["fc"]))
                    self.assertGreater(len(set(outputs["fc"])), 1)

    def test_deterministic_files_and_distinct_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            golden.generate(first)
            golden.generate(second)
            for path in first.iterdir():
                self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
            golden.generate(second, seed=8)
            self.assertNotEqual((first / "input.hex").read_bytes(), (second / "input.hex").read_bytes())
            self.assertNotEqual((first / "weights.hex").read_bytes(), (second / "weights.hex").read_bytes())

    def test_default_hand_stable_witness_outputs(self):
        outputs = golden.run_model(golden.build_model())
        self.assertEqual(outputs["gap"], [7, 13, 19, 1, 5, 0, 4, 0])
        self.assertEqual(outputs["fc"], [-10, 36, -27, -14, 86, 69])
        # First two FC channels deliberately have one unit tap and one bias.
        self.assertEqual(outputs["fc"][:2], [outputs["gap"][0] - 17, outputs["gap"][1] + 23])

    def test_cli_flags_and_json_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run([sys.executable, str(MODULE_PATH), "--output", tmp,
                                        "--seed", "19", "--classes", "4", "--pattern", "impulse"],
                                       check=True, capture_output=True, text=True)
            summary = json.loads(completed.stdout)
            self.assertEqual((summary["seed"], summary["classes"], summary["pattern"]), (19, 4, "impulse"))
            self.assertEqual(len(summary["fc"]), 4)
            self.assertEqual(len((Path(tmp) / "golden_fc.hex").read_text().splitlines()), 4)
            lines = (Path(tmp) / "input.hex").read_text().splitlines()
            self.assertEqual((lines[0], lines[168], lines[319]), ("80", "7f", "3f"))

    def test_hardware_valid_class_counts_and_bad_arguments(self):
        for classes in (1, 8):
            self.assertEqual(len(golden.run_model(golden.build_model(classes=classes))["fc"]), classes)
        for classes in (0, 9):
            with self.assertRaises(ValueError):
                golden.build_model(classes=classes)
        with self.assertRaises(ValueError):
            golden.build_model(pattern="unknown")


if __name__ == "__main__":
    unittest.main()
