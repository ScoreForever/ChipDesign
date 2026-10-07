#!/usr/bin/env python3
"""Generate chipdesign_requant_test.hex — Matrix -> Requant -> Vector chain test.

The program runs a 4x8 INT8 GEMM with weights=1 and activations=1, producing
INT32 output 4 in every column.  TFLite requant with bias=0, Q0.31 multiplier
0.5, shift=+1, offset=0 converts this to INT8 4, auto-copies it into the
Vector Unit's src_a, then performs an INT8 vector ADD with src_b=1.  Expected
result is 5 in every lane.
"""

REGS = {
    'zero': 0,  'ra': 1,   'sp': 2,   'gp': 3,
    'tp': 4,   't0': 5,   't1': 6,   't2': 7,
    's0': 8,   's1': 9,   'a0': 10,  'a1': 11,
    'a2': 12,  'a3': 13,  'a4': 14,  'a5': 15,
    'a6': 16,  'a7': 17,  's2': 18,  's3': 19,
    's4': 20,  's5': 21,  's6': 22,  's7': 23,
    's8': 24,  's9': 25,  's10': 26, 's11': 27,
    't3': 28,  't4': 29,  't5': 30,  't6': 31,
}


def reg(r):
    return REGS[r]


def uimm12(v):
    return v & 0xFFF


def lui(rd, imm):
    return ((imm & 0xFFFFF) << 12) | (reg(rd) << 7) | 0b0110111


def addi(rd, rs1, imm):
    imm = uimm12(imm)
    return (imm << 20) | (reg(rs1) << 15) | (0b000 << 12) | (reg(rd) << 7) | 0b0010011


def sw(rs2, imm, rs1):
    imm = uimm12(imm)
    return (((imm >> 5) & 0x7F) << 25) | (reg(rs2) << 20) | (reg(rs1) << 15) | (0b010 << 12) | ((imm & 0x1F) << 7) | 0b0100011


def lw(rd, imm, rs1):
    imm = uimm12(imm)
    return (imm << 20) | (reg(rs1) << 15) | (0b010 << 12) | (reg(rd) << 7) | 0b0000011


def andi(rd, rs1, imm):
    imm = uimm12(imm)
    return (imm << 20) | (reg(rs1) << 15) | (0b111 << 12) | (reg(rd) << 7) | 0b0010011


def beq(rs1, rs2, label_pc, cur_pc):
    imm = label_pc - cur_pc
    imm12 = (imm >> 12) & 1
    imm10_5 = (imm >> 5) & 0x3F
    imm4_1 = (imm >> 1) & 0xF
    imm11 = (imm >> 11) & 1
    return (imm12 << 31) | (imm10_5 << 25) | (reg(rs2) << 20) | (reg(rs1) << 15) | (0b000 << 12) | (imm4_1 << 8) | (imm11 << 7) | 0b1100011


def bne(rs1, rs2, label_pc, cur_pc):
    imm = label_pc - cur_pc
    imm12 = (imm >> 12) & 1
    imm10_5 = (imm >> 5) & 0x3F
    imm4_1 = (imm >> 1) & 0xF
    imm11 = (imm >> 11) & 1
    return (imm12 << 31) | (imm10_5 << 25) | (reg(rs2) << 20) | (reg(rs1) << 15) | (0b001 << 12) | (imm4_1 << 8) | (imm11 << 7) | 0b1100011


def j(label_pc, cur_pc):
    imm = label_pc - cur_pc
    imm20 = (imm >> 20) & 1
    imm10_1 = (imm >> 1) & 0x3FF
    imm11 = (imm >> 11) & 1
    imm19_12 = (imm >> 12) & 0xFF
    return (imm20 << 31) | (imm10_1 << 21) | (imm11 << 20) | (imm19_12 << 12) | (0 << 7) | 0b1101111


def main():
    base = 0x80000000
    ops = []
    labels = {}
    pc = base

    def mark(name):
        labels[name] = pc

    def op(name, *args):
        nonlocal pc
        ops.append((name, args))
        pc += 4

    mark('_start')

    # Magic status region base: 0x80002000 - 32 = 0x80001FE0
    op('lui', 't6', 0x80002)

    # Write RUNNING = 0x12345678
    op('lui', 't1', 0x12345)
    op('addi', 't1', 't1', 0x678)
    op('sw', 't1', -32, 't6')

    # NPU base = 0x70000000
    op('lui', 't0', 0x70000)

    # Weight words = 0x01010101 for all 8 words
    op('lui', 't1', 0x01010)
    op('addi', 't1', 't1', 0x101)
    for off in [0x40, 0x44, 0x48, 0x4C, 0x50, 0x54, 0x58, 0x5C]:
        op('sw', 't1', off, 't0')

    # Trigger weight load
    op('addi', 't1', 'zero', 2)
    op('sw', 't1', 0, 't0')

    # Poll until weights_loaded (STATUS bit 1)
    mark('poll_wg')
    op('lw', 't1', 4, 't0')
    op('andi', 't1', 't1', 2)
    op('beq', 't1', 'zero', 'poll_wg')

    # Activation = 0x01010101
    op('lui', 't1', 0x01010)
    op('addi', 't1', 't1', 0x101)
    op('sw', 't1', 0x100, 't0')

    # Partial sums = 0
    for off in [0x110, 0x114, 0x118, 0x11C, 0x120, 0x124, 0x128, 0x12C]:
        op('sw', 'zero', off, 't0')

    # Requant bias = 0 for all 8 channels
    for off in [0x310, 0x314, 0x318, 0x31C, 0x320, 0x324, 0x328, 0x32C]:
        op('sw', 'zero', off, 't0')

    # Requant multiplier = 0x40000000 (Q0.31 = 0.5) for all 8 channels
    op('lui', 't1', 0x40000)
    for off in [0x330, 0x334, 0x338, 0x33C, 0x340, 0x344, 0x348, 0x34C]:
        op('sw', 't1', off, 't0')

    # Requant shift = +1 (left shift 1), output offset = 0
    op('addi', 't1', 'zero', 1)
    op('sw', 't1', 0x350, 't0')
    op('sw', 'zero', 0x354, 't0')

    # Requant control: enable + copy to vec_src_a
    op('addi', 't1', 'zero', 3)
    op('sw', 't1', 0x300, 't0')

    # Start Matrix compute
    op('addi', 't1', 'zero', 1)
    op('sw', 't1', 0, 't0')

    # Poll until requant done (STATUS bit 0)
    mark('poll_rq')
    op('lw', 't1', 0x304, 't0')
    op('andi', 't1', 't1', 1)
    op('beq', 't1', 'zero', 'poll_rq')

    # Vector src_b = 0x01010101 (two words for 8 lanes)
    op('lui', 't1', 0x01010)
    op('addi', 't1', 't1', 0x101)
    op('sw', 't1', 0x028, 't0')
    op('sw', 't1', 0x02C, 't0')

    # Vector ctrl = ADD, src_a=VECTOR_A, src_b=VECTOR_B, dst=OUTPUT
    op('sw', 'zero', 0x008, 't0')
    op('addi', 't1', 'zero', 0xFF)
    op('sw', 't1', 0x00C, 't0')

    # Trigger vector op
    op('addi', 't1', 'zero', 1)
    op('sw', 't1', 0x018, 't0')

    # Poll until vector out_valid (STATUS bit 0)
    mark('poll_vu')
    op('lw', 't1', 0x014, 't0')
    op('andi', 't1', 't1', 1)
    op('beq', 't1', 'zero', 'poll_vu')

    # Read vector output and verify 0x05050505 in both LO and HI
    op('lw', 't2', 0x030, 't0')
    op('lui', 't3', 0x05050)
    op('addi', 't3', 't3', 0x505)
    op('bne', 't2', 't3', 'fail')

    op('lw', 't2', 0x034, 't0')
    op('bne', 't2', 't3', 'fail')

    # PASS
    op('lui', 't1', 0xC0DEC)
    op('addi', 't1', 't1', 0x0DE)
    op('sw', 't1', -32, 't6')

    mark('done')
    op('j', 'done')

    mark('fail')
    op('lui', 't1', 0xDEADC)
    op('addi', 't1', 't1', -0x111)
    op('sw', 't1', -32, 't6')
    op('j', 'done')

    encoders = {
        'lui': lui, 'addi': addi, 'sw': sw, 'lw': lw,
        'andi': andi, 'beq': beq, 'bne': bne, 'j': j,
    }

    pc = base
    out = ["// Auto-generated from chipdesign_requant_test.S by gen_requant_hex.py"]
    out.append(f"// labels: { {k: hex(v) for k, v in labels.items()} }")
    for name, args in ops:
        if name in ('beq', 'bne'):
            inst = encoders[name](args[0], args[1], labels[args[2]], pc)
        elif name == 'j':
            inst = encoders[name](labels[args[0]], pc)
        else:
            inst = encoders[name](*args)
        out.append(f"{inst:08x}")
        pc += 4

    return '\n'.join(out) + '\n'


if __name__ == '__main__':
    print(main())
