"""整数 Requant 参考实现，与 RTL `requant_unit.sv` 逐位一致。

导出器、Python 黄金模型、RTL 三方必须共享这里的函数。任何一处用浮点近似
都会导致逐层 LSB 不一致（ARCHITECTURE.md 第 7 节）。
"""

INT32_MIN = -2 ** 31
INT32_MAX = 2 ** 31 - 1


def _int32(x):
    """把任意整数截断为 signed INT32（两补码 wrap）。"""
    x = int(x) & 0xFFFFFFFF
    return x - 2 ** 32 if x >= 2 ** 31 else x


def quantize_multiplier(real_m):
    """编码 real_m ~= multiplier/2^31 * 2^shift。

    与 TFLite/gemmlowp `QuantizeMultiplier` 一致，shift 范围 [-31, 31]。
    """
    import math
    if not math.isfinite(real_m) or real_m < 0:
        raise ValueError(f'real multiplier must be finite and non-negative, got {real_m}')
    if real_m == 0:
        return 0, 0
    mantissa, exp = math.frexp(real_m)      # real_m = mantissa * 2^exp, mantissa in [0.5, 1)
    # mantissa 为正；显式 half-away-from-zero，与 C++ std::round/TFLite 一致，
    # 不依赖 Python round 的 ties-to-even 规则。
    m = int(math.floor(mantissa * (2 ** 31) + 0.5))
    if m == 2 ** 31:                        # 舍入到 1.0 时归一
        m = 2 ** 30
        exp += 1
    if not -31 <= exp <= 31:
        raise ValueError(f'quantized shift {exp} outside RTL range [-31, 31]')
    return m, exp


def saturating_left_shift(value, amount):
    value = _int32(value)
    amount = int(amount)
    if amount <= 0:
        return value
    wide = value << amount
    if wide > INT32_MAX:
        return INT32_MAX
    if wide < INT32_MIN:
        return INT32_MIN
    return _int32(wide)


def saturating_rounding_doubling_high_mul(a, b):
    a = _int32(a)
    b = _int32(b)
    if a == INT32_MIN and b == INT32_MIN:
        return INT32_MAX
    product = a * b
    nudge = 1073741824 if product >= 0 else -1073741823
    rounded = product + nudge
    if rounded >= 0:
        magnitude = rounded >> 31
    else:
        magnitude = -((-rounded) >> 31)
    return _int32(magnitude)


def rounding_divide_by_pot(value, exponent):
    value = _int32(value)
    exponent = int(exponent)
    if exponent <= 0:
        return value
    mask = (1 << exponent) - 1
    remainder = value & mask
    threshold = (mask >> 1) + (1 if value < 0 else 0)
    base = value >> exponent                    # 算术右移（floor）
    return base + (1 if remainder > threshold else 0)


def multiply_by_quantized_multiplier(value, multiplier, shift):
    value = _int32(value)
    multiplier = int(multiplier)
    shift = int(shift)
    left = shift if shift > 0 else 0
    right = -shift if shift < 0 else 0
    shifted = saturating_left_shift(value, left)
    high = saturating_rounding_doubling_high_mul(shifted, multiplier)
    return rounding_divide_by_pot(high, right)


def requant(acc, bias, multiplier, shift, output_offset, activation_min, activation_max):
    """对单通道 INT32 累加值做 TFLite 风格 requant，返回 INT8。"""
    biased = _int32(acc + bias)
    scaled = multiply_by_quantized_multiplier(biased, multiplier, shift)
    offset = scaled + output_offset
    if offset < activation_min:
        return activation_min
    if offset > activation_max:
        return activation_max
    return offset


if __name__ == '__main__':
    # 自检：multiplier=0.5(Q0.31), shift=+1, acc=4 -> 4
    m, s = quantize_multiplier(0.5)
    assert m == 0x40000000 and s == 0, (m, s)
    out = requant(4, 0, m, 1, 0, -128, 127)
    print(f'acc=4 -> requant={out} (expect 4)')
    assert out == 4
    print('quantize.py self-check OK')
