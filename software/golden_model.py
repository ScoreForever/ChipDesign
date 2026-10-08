"""TinyCNN-8 整数黄金模型，与 RTL `tinycnn8_npu_top` 逐位一致。

只用整数运算，复刻 RTL 的卷积 / MaxPool / NHWC Flatten / FC 与 requant 语义。
输入/权重布局与 NPU 完全一致（NHWC，权重 packed 布局），
是导出器、Python 黄金模型、RTL 三方 bit-exact 验证的基准。
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
from quantize import requant, _int32  # noqa: E402


def _conv2d_int8(inp, W, bias, mult, shift, out_offset, act_min, act_max, stride=1, pad=1):
    """inp: (H, W, Cin) int8；W: (KH, KW, Cin, Cout) int8。"""
    H, Ww, Cin = inp.shape
    KH, KW, _, Cout = W.shape
    if stride != 1:
        raise ValueError('current TinyCNN-8 golden model only supports conv stride=1')
    # 向量化乘加只改变计算速度，不改变整数结果：本网络的最坏累加值远小于
    # INT32 上限，因此 int64 求和后转 INT32 与 RTL 逐项累加等价。
    padded = np.pad(inp.astype(np.int64), ((pad, pad), (pad, pad), (0, 0)))
    windows = np.lib.stride_tricks.sliding_window_view(
        padded, (KH, KW), axis=(0, 1)
    )  # (H,W,Cin,KH,KW)
    acc = np.tensordot(
        windows, W.astype(np.int64), axes=((2, 3, 4), (2, 0, 1))
    )
    acc += np.asarray(bias, dtype=np.int64).reshape(1, 1, Cout)
    out = np.zeros((H, Ww, Cout), dtype=np.int8)
    for oc in range(Cout):
        for oy in range(H):
            for ox in range(Ww):
                out[oy, ox, oc] = requant(
                    _int32(acc[oy, ox, oc]), 0, mult[oc], shift[oc],
                    out_offset, act_min, act_max,
                )
    return out


def _maxpool2x2(inp):
    H, W, C = inp.shape
    out = np.zeros((H // 2, W // 2, C), dtype=np.int8)
    for c in range(C):
        for y in range(H // 2):
            for x in range(W // 2):
                out[y, x, c] = inp[2 * y:2 * y + 2, 2 * x:2 * x + 2, c].max()
    return out


def _fc_int8(inp, W, bias):
    """inp: (Cin,) int8；W: (Cin, Cout) int8；bias: (Cout,) int32 -> (Cout,) int32 logits。"""
    Cin = inp.shape[0]
    Cout = bias.shape[0]          # 有效输出数 = num_classes（fc_w 有 8 个 lane，仅前 Cout 有效）
    out = np.zeros(Cout, dtype=np.int32)
    for oc in range(Cout):
        acc = int(bias[oc])
        for ic in range(Cin):
            acc += int(inp[ic]) * int(W[ic, oc])
        out[oc] = _int32(acc)
    return out


def unpack_conv1(packed):
    """packed: (9, 8) [spatial, oc] -> (3, 3, 1, 8) [kh, kw, ic, oc]。"""
    W = np.zeros((3, 3, 1, 8), dtype=np.int8)
    for kh in range(3):
        for kw in range(3):
            W[kh, kw, 0, :] = packed[kh * 3 + kw, :]
    return W


def unpack_conv2(packed):
    """packed: (72, 8) [spatial*8+ic, oc] -> (3, 3, 8, 8) [kh, kw, ic, oc]。"""
    W = np.zeros((3, 3, 8, 8), dtype=np.int8)
    for kh in range(3):
        for kw in range(3):
            for ic in range(8):
                W[kh, kw, ic, :] = packed[(kh * 3 + kw) * 8 + ic, :]
    return W


def infer(pkg, input_int8):
    """pkg: 导出包 dict；input_int8: (20, 16) 或 (320,) int8 -> (class_count,) int32 logits。"""
    if np.asarray(pkg.get('model_head', '')).item() != 'flatten':
        raise ValueError('golden model accepts only the Flatten deployment ABI')
    if int(np.asarray(pkg['num_classes']).item()) != 4:
        raise ValueError('production deployment package must have four classes')
    if np.asarray(pkg['fc_w']).shape != (160, 8):
        raise ValueError('Flatten deployment ABI requires FC weights shaped 160x8')
    inp = np.asarray(input_int8, dtype=np.int8).reshape(20, 16, 1)

    c1 = _conv2d_int8(inp, unpack_conv1(pkg['conv1_w']), pkg['conv1_bias'],
                      pkg['conv1_mult'], pkg['conv1_shift'], 0, 0, 127)   # (20,16,8)
    p1 = _maxpool2x2(c1)                                                   # (10,8,8)
    c2 = _conv2d_int8(p1, unpack_conv2(pkg['conv2_w']), pkg['conv2_bias'],
                      pkg['conv2_mult'], pkg['conv2_shift'], 0, 0, 127)   # (10,8,8)
    p2 = _maxpool2x2(c2)                                                   # (5,4,8)
    flat = p2.reshape(-1)                                                   # NHWC (160,)
    logits = _fc_int8(flat, pkg['fc_w'], pkg['fc_bias'])                   # (class_count,)
    return logits


def argmax_with_fc_compare(logits, fc_cmp_mult, fc_cmp_shift):
    """CPU 侧：用 fc_compare 参数把各类 logit 统一尺度后 argmax。"""
    from quantize import multiply_by_quantized_multiplier
    scores = [multiply_by_quantized_multiplier(int(logits[c]), int(fc_cmp_mult[c]), int(fc_cmp_shift[c]))
              for c in range(len(logits))]
    return int(np.argmax(scores))
