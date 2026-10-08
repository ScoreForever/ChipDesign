"""黄金模型 vs RTL testbench 合成参数的 bit-exact 回归。

用 tb_tinycnn8_npu_top.sv 里相同的合成权重/参数构造部署包，喂全 2 输入，
校验黄金模型 logits 与 RTL testbench 期望公式逐位一致。
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import golden_model as gm  # noqa: E402


def build_synthetic_pkg():
    pkg = {'num_classes': 4, 'model_head': 'flatten'}
    conv1_w = np.zeros((9, 8), dtype=np.int8)
    conv1_w[4, :] = 1                      # Conv1 中心 tap = 1
    pkg['conv1_w'] = conv1_w
    pkg['conv1_bias'] = np.arange(8, dtype=np.int32)
    pkg['conv1_mult'] = np.full(8, 0x40000000, dtype=np.int32)
    pkg['conv1_shift'] = np.full(8, 1, dtype=np.int8)

    conv2_w = np.zeros((72, 8), dtype=np.int8)
    for ic in range(8):                    # Conv2 中心 1x1 通道恒等
        conv2_w[4 * 8 + ic, ic] = 1
    pkg['conv2_w'] = conv2_w
    pkg['conv2_bias'] = np.zeros(8, dtype=np.int32)
    pkg['conv2_mult'] = np.full(8, 0x40000000, dtype=np.int32)
    pkg['conv2_shift'] = np.full(8, 1, dtype=np.int8)

    fc_w = np.zeros((160, 8), dtype=np.int8)
    for ic in range(160):
        for oc in range(4):
            fc_w[ic, oc] = ((ic + oc) % 3) - 1
    pkg['fc_w'] = fc_w
    pkg['fc_bias'] = np.array([o - 2 for o in range(4)], dtype=np.int32)
    return pkg


def expected_logits():
    out = []
    for o in range(4):
        e = o - 2
        for ic in range(160):
            e += (2 + (ic % 8)) * (((ic + o) % 3) - 1)
        out.append(e)
    return out


def main():
    pkg = build_synthetic_pkg()
    inp = np.full((20, 16), 2, dtype=np.int8)
    logits = gm.infer(pkg, inp)
    exp = expected_logits()
    print('golden logits:', logits.tolist())
    print('expected     :', exp)
    ok = all(int(a) == b for a, b in zip(logits, exp))
    if not ok:
        for i, (a, b) in enumerate(zip(logits, exp)):
            if int(a) != b:
                print(f'  MISMATCH class {i}: got {a} expected {b}')
        sys.exit(1)
    print('PASS: golden model matches RTL testbench expectation')


if __name__ == '__main__':
    main()
