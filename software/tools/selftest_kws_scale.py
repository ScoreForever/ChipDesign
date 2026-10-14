"""Compare a host build of kws.c's integer scale function with quantize.py."""
import argparse
import ctypes
import random
from quantize import multiply_by_quantized_multiplier


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--library', required=True, help='host shared library built from runtime/kws.c')
    args = parser.parse_args()
    library = ctypes.CDLL(args.library)
    scale = library.kws_scale
    scale.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_int]
    scale.restype = ctypes.c_int32
    values = [-2**31, -2**31 + 1, -65537, -3, -2, -1, 0, 1, 2, 3, 65537, 2**31 - 1]
    multipliers = [-2**31, -2**31+1, -1, 0, 1, 2**30, 2**31 - 1]
    cases = [(v, m, s) for v in values for m in multipliers for s in range(-31, 32)]
    rng = random.Random(17)
    cases += [(rng.randrange(-2**31, 2**31), rng.randrange(0, 2**31), rng.randrange(-31, 32))
              for _ in range(10000)]
    for value, multiplier, shift in cases:
        got = scale(value, multiplier, shift)
        expected = multiply_by_quantized_multiplier(value, multiplier, shift)
        assert got == expected, (value, multiplier, shift, got, expected)
    print(f'PASS: {len(cases)} native C/Python scale comparisons, shifts -31..31 and INT32 extremes')


if __name__ == '__main__':
    main()
