#!/usr/bin/env python3
"""Generate chipdesign_dma_test.hex — NPU DMA test.

The program writes weight data into SRAM, then uses the NPU DMA engine to copy
those 8 words into the NPU MATRIX_WEIGHT staging registers.  After that it
runs the usual 4x8 INT8 GEMM and checks the first output column is 4.
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

    # SRAM base for DMA source data: 0x80001000
    op('lui', 't5', 0x80001)

    # Write 8 weight words = 0x01010101 into SRAM
    op('lui', 't1', 0x01010)
    op('addi', 't1', 't1', 0x101)
    for off in range(0, 32, 4):
        op('sw', 't1', off, 't5')

    # NPU base = 0x70000000
    op('lui', 't0', 0x70000)

    # Configure DMA: SRC=0x80001000, DST=0x70000040, LEN=8
    op('lui', 't1', 0x80001)
    op('sw', 't1', 0x400, 't0')
    op('addi', 't1', 't0', 0x040)
    op('sw', 't1', 0x404, 't0')
    op('addi', 't1', 'zero', 8)
    op('sw', 't1', 0x408, 't0')

    # Start DMA
    op('addi', 't1', 'zero', 1)
    op('sw', 't1', 0x40C, 't0')

    # Poll until DMA done (STATUS bit 1)
    mark('poll_dma')
    op('lw', 't1', 0x410, 't0')
    op('andi', 't1', 't1', 2)
    op('beq', 't1', 'zero', 'poll_dma')

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

    # Start compute
    op('addi', 't1', 'zero', 1)
    op('sw', 't1', 0, 't0')

    # Poll until out_valid (STATUS bit 2)
    mark('poll_out')
    op('lw', 't1', 4, 't0')
    op('andi', 't1', 't1', 4)
    op('beq', 't1', 'zero', 'poll_out')

    # Read first output column and verify == 4
    op('lw', 't2', 0x200, 't0')
    op('addi', 't3', 'zero', 4)
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
    out = ["// Auto-generated from chipdesign_dma_test.S by gen_dma_hex.py"]
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
