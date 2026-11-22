#!/usr/bin/env python3
"""Innovation 1 Verification: BitNet-1.58b Ternary (t2), Signed INT2 (i2), Symmetric INT2 (s2),
and Layer-Wise Mixed-Precision + ABI 6.0 Early-Exit (Icarus Verilog)."""
from __future__ import annotations

import random
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'software/host'))
import npu_program as abi  # noqa: E402
from hardware.npu.scripts.test_abi6_early_exit import build_sim, cmd_line, run_trace, write_load_model  # noqa: E402


def write_check_infer(lines, pkg: abi.Package, values, expected_out):
    m = pkg.manifest
    raw_in = abi.encode_tensor(values, m['input_dtype'])
    raw_in += b'\x00' * ((-len(raw_in)) % 4)
    lines.append(cmd_line(5))  # INPUT_BEGIN
    for off in range(0, len(raw_in), 4):
        word = struct.unpack_from('<I', raw_in, off)[0]
        lines.append(cmd_line(2, 0x20000 + m['input_base'] + off, word))
    lines.append(cmd_line(6))  # INPUT_COMMIT
    lines.append(cmd_line(7))  # START
    lines.append(cmd_line(0xf0))  # WAIT IRQ
    lines.append(cmd_line(1, 0x04, mask=0x7f, value=0x0a))  # done=1, faulted=0
    raw_out = abi.encode_tensor(expected_out, m['output_dtype'])
    raw_out += b'\x00' * ((-len(raw_out)) % 4)
    for off in range(0, len(raw_out), 4):
        word = struct.unpack_from('<I', raw_out, off)[0]
        valid_bytes = min(4, m['output_bytes'] - off)
        mask = (1 << (8 * valid_bytes)) - 1
        lines.append(cmd_line(1, 0x20000 + m['output_base'] + off, mask=mask, value=word & mask))
    lines.append(cmd_line(1, 0x38, mask=0, value=0))  # cycles
    lines.append(cmd_line(8))  # ACK


def run_model_test(sim: Path, out: Path, name: str, model: dict, inp: list[int], out_tensor: str):
    pkg = abi.compile_model(model)
    ref = abi.reference(model, inp)
    lines = []
    write_load_model(lines, pkg)
    lines.append(cmd_line(4))  # MODEL_COMMIT
    write_check_infer(lines, pkg, inp, ref[out_tensor])
    run_trace(sim, out / f'{name}.trace', lines)
    return pkg


def main():
    rng = random.Random(2026)
    out = ROOT / 'build/abi6_w2_bitnet_test'
    out.mkdir(parents=True, exist_ok=True)
    sim = build_sim(out)

    # 1. BitNet-1.58b Ternary (t2: {-1, 0, +1}) Conv + DWConv + FC vs INT4 size comparison
    def make_conv_net(wdtype, choices):
        return {
            'input': {'name': 'x', 'shape': [6, 6, 16], 'dtype': 'i4'},
            'nodes': [
                {'op': 'Conv', 'input': 'x', 'output': 'c1', 'channels': 16, 'kernel': 3, 'stride': 2, 'pads': [1, 1, 1, 1],
                 'weight_dtype': wdtype,
                 'weights': [rng.choice(choices) for _ in range(16 * 9 * 16)],
                 'bias': [rng.randint(-50, 50) for _ in range(16)]},
                {'op': 'Requant', 'input': 'c1', 'output': 'q1', 'dtype': 'u4',
                 'multiplier': [0x4000] * 16, 'shift': [-3] * 16},
                {'op': 'ReLU', 'input': 'q1', 'output': 'r1'},
                {'op': 'Conv', 'input': 'r1', 'output': 'dw1', 'channels': 16, 'groups': 16, 'kernel': 3, 'pads': [1, 1, 1, 1],
                 'weight_dtype': wdtype,
                 'weights': [rng.choice(choices) for _ in range(16 * 9)],
                 'bias': [rng.randint(-20, 20) for _ in range(16)]},
                {'op': 'Requant', 'input': 'dw1', 'output': 'q2', 'dtype': 'i4',
                 'multiplier': [0x4000] * 16, 'shift': [-2] * 16},
                {'op': 'AvgPool', 'input': 'q2', 'output': 'gap', 'kernel': 3, 'stride': 3},
                {'op': 'Requant', 'input': 'gap', 'output': 'gap_q', 'dtype': 'i4',
                 'multiplier': [0x4000] * 16, 'shift': [-2] * 16},
                {'op': 'FC', 'input': 'gap_q', 'output': 'logits', 'channels': 10,
                 'weight_dtype': wdtype,
                 'weights': [rng.choice(choices) for _ in range(10 * 16)],
                 'bias': [rng.randint(-30, 30) for _ in range(10)]},
            ],
        }

    inp_6x6 = [rng.randint(-8, 7) for _ in range(6 * 6 * 16)]
    m_t2 = make_conv_net('t2', [-1, 0, 1])
    m_i4 = make_conv_net('i4', [-1, 0, 1])
    pkg_t2 = run_model_test(sim, out, 't1_bitnet_t2', m_t2, inp_6x6, 'logits')
    pkg_i4 = run_model_test(sim, out, 't1_int4_ref', m_i4, inp_6x6, 'logits')
    b_t2, b_i4 = pkg_t2.manifest['model_bytes'], pkg_i4.manifest['model_bytes']
    print(f'PASS Test 1: BitNet-1.58b Ternary (t2) Conv+DWConv+FC 0-bit exact! '
          f'model_bytes: {b_i4} B (i4) -> {b_t2} B (t2) ({(1 - b_t2/b_i4)*100:.1f}% total reduction, ~2.0x weight compression)')

    # 2. Signed INT2 (i2: {-2, -1, 0, +1}) with oc=12 (full 8-col + narrow 4-col tail)
    m_i2 = {
        'input': {'name': 'x', 'shape': [4, 4, 8], 'dtype': 'i4'},
        'nodes': [
            {'op': 'Conv', 'input': 'x', 'output': 'c1', 'channels': 12, 'kernel': 3, 'pads': [1, 1, 1, 1],
             'weight_dtype': 'i2',
             'weights': [rng.choice([-2, -1, 0, 1]) for _ in range(12 * 9 * 8)],
             'bias': [rng.randint(-40, 40) for _ in range(12)]},
            {'op': 'Requant', 'input': 'c1', 'output': 'q1', 'multiplier': [0x4000] * 12, 'shift': [-3] * 12},
            {'op': 'AvgPool', 'input': 'q1', 'output': 'g1', 'kernel': 4, 'stride': 4},
            {'op': 'Requant', 'input': 'g1', 'output': 'gq', 'multiplier': [0x4000] * 12, 'shift': [-2] * 12},
            {'op': 'FC', 'input': 'gq', 'output': 'out', 'channels': 5, 'weight_dtype': 'i2',
             'weights': [rng.choice([-2, -1, 0, 1]) for _ in range(5 * 12)],
             'bias': [1, -2, 3, -4, 5]},
        ],
    }
    run_model_test(sim, out, 't2_signed_i2', m_i2, [rng.randint(-8, 7) for _ in range(4 * 4 * 8)], 'out')
    print('PASS Test 2: Signed INT2 (i2: {-2,-1,0,+1}) Conv(oc=12) + FC(oc=5) 0-bit exact!')

    # 3. Symmetric INT2 (s2: {-2, -1, +1, +2}) with oc=10 (zero-free alphabet + narrow tail)
    m_s2 = {
        'input': {'name': 'x', 'shape': [4, 4, 8], 'dtype': 'u4'},
        'nodes': [
            {'op': 'Conv', 'input': 'x', 'output': 'c1', 'channels': 10, 'kernel': 3, 'pads': [1, 1, 1, 1],
             'weight_dtype': 's2',
             'weights': [rng.choice([-2, -1, 1, 2]) for _ in range(10 * 9 * 8)],
             'bias': [rng.randint(-30, 30) for _ in range(10)]},
            {'op': 'Requant', 'input': 'c1', 'output': 'q1', 'multiplier': [0x4000] * 10, 'shift': [-4] * 10},
            {'op': 'AvgPool', 'input': 'q1', 'output': 'g1', 'kernel': 4, 'stride': 4},
            {'op': 'Requant', 'input': 'g1', 'output': 'gq', 'multiplier': [0x4000] * 10, 'shift': [-2] * 10},
            {'op': 'FC', 'input': 'gq', 'output': 'out', 'channels': 6, 'weight_dtype': 's2',
             'weights': [rng.choice([-2, -1, 1, 2]) for _ in range(6 * 10)],
             'bias': [0] * 6},
        ],
    }
    run_model_test(sim, out, 't3_sym_s2', m_s2, [rng.randint(0, 15) for _ in range(4 * 4 * 8)], 'out')
    print('PASS Test 3: Symmetric INT2 (s2: {-2,-1,+1,+2}) Conv(oc=10) + FC(oc=6) 0-bit exact!')

    # 4. Combined Innovation: Layer-Wise Mixed Precision (i4 -> t2 -> s2 -> Exit -> i2) in ONE model.bin!
    m_mix = {
        'input': {'name': 'x', 'shape': [1, 1, 8], 'dtype': 'i4'},
        'nodes': [
            {'op': 'FC', 'input': 'x', 'output': 'e1_raw', 'channels': 4, 'weight_dtype': 't2',
             'weights': [[1, 1, 0, 0, 0, 0, 0, 0], [0, 0, 1, 1, 0, 0, 0, 0],
                         [0, 0, 0, 0, 1, 1, 0, 0], [0, 0, 0, 0, 0, 0, 1, 1]],
             'bias': [0, 0, 0, 0]},
            {'op': 'Exit', 'input': 'e1_raw', 'output': 'e1_chk', 'mode': 'margin', 'threshold': 8, 'exit_id': 3},
            {'op': 'FC', 'input': 'x', 'output': 'h_raw', 'channels': 8, 'weight_dtype': 's2',
             'weights': [[(2 if i == j else -1) for j in range(8)] for i in range(8)], 'bias': [0] * 8},
            {'op': 'Requant', 'input': 'h_raw', 'output': 'h_q', 'multiplier': [0x4000] * 8, 'shift': [-2] * 8},
            {'op': 'FC', 'input': 'h_q', 'output': 'out', 'channels': 4, 'weight_dtype': 'i2',
             'weights': [rng.choice([-2, -1, 0, 1]) for _ in range(4 * 8)], 'bias': [0, 1, -1, 2]},
        ],
    }
    pkg_mix = abi.compile_model(m_mix)
    easy_in = [6, 6, 0, 0, 0, 0, 0, 0]   # t2 e1 = [12, 0, 0, 0], margin=12 >= 8 -> early exit!
    hard_in = [2, 1, 2, 0, 0, 0, 0, 0]   # t2 e1 = [3, 2, 0, 0], margin=1 < 8 -> s2 + i2 deep path!
    ref_e = abi.reference(m_mix, easy_in)
    ref_h = abi.reference(m_mix, hard_in)
    assert ref_e['__exit__']['taken'] is True and ref_h['__exit__']['taken'] is False
    lines = []
    write_load_model(lines, pkg_mix)
    lines.append(cmd_line(4))
    write_check_infer(lines, pkg_mix, easy_in, ref_e['out'])
    write_check_infer(lines, pkg_mix, hard_in, ref_h['out'])
    run_trace(sim, out / 't4_mixed_precision_early_exit.trace', lines)
    print('PASS Test 4: Combined Layer-Wise Mixed-Precision (t2 + Exit + s2 + i2) in ONE model.bin 0-bit exact!')

    # 5. Negative check: non-zero w_mode on ReLU must fail MODEL_COMMIT with error=18
    m_bad = {'input': {'name': 'x', 'shape': [1, 1, 8], 'dtype': 'i4'}, 'nodes': [{'op': 'ReLU', 'input': 'x', 'output': 'y'}]}
    pkg_bad = abi.compile_model(m_bad)
    img_bad = bytearray(pkg_bad.image)
    w0 = struct.unpack_from('<I', img_bad, 0)[0]
    struct.pack_into('<I', img_bad, 0, w0 | (1 << 20))
    lines = []
    write_load_model(lines, abi.Package(bytes(img_bad), pkg_bad.manifest))
    lines.append(cmd_line(4, error=18))
    run_trace(sim, out / 't5_neg_wmode_on_relu.trace', lines)
    print('PASS Test 5 (neg_wmode_on_relu): rejected cleanly at MODEL_COMMIT with error=18 (basic_bad)')

    print('\nALL INNOVATION-1 (BITNET-1.58b / INT2 MIXED-PRECISION) RTL TESTS PASSED!')


if __name__ == '__main__':
    main()
