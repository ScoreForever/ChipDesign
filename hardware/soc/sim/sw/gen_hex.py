#!/usr/bin/env python3
"""Minimal RISC-V assembler for the ChipDesign NPU test program.
Only implements the RV32I instructions used by chipdesign_npu_test.S."""

import sys

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
    v = v & 0xFFF
    if v & 0x800:
        v -= 0x1000
    return v


def lui(rd, imm):
    return ((imm & 0xFFFFF) << 12) | (reg(rd) << 7) | 0b0110111


def addi(rd, rs1, imm):
    imm = uimm12(imm)
    return ((imm & 0xFFF) << 20) | (reg(rs1) << 15) | (0b000 << 12) | (reg(rd) << 7) | 0b0010011


def sw(rs2, imm, rs1):
    imm = uimm12(imm)
    return (((imm >> 5) & 0x7F) << 25) | (reg(rs2) << 20) | (reg(rs1) << 15) | (0b010 << 12) | ((imm & 0x1F) << 7) | 0b0100011


def lw(rd, imm, rs1):
    imm = uimm12(imm)
    return ((imm & 0xFFF) << 20) | (reg(rs1) << 15) | (0b010 << 12) | (reg(rd) << 7) | 0b0000011


def andi(rd, rs1, imm):
    imm = uimm12(imm)
    return ((imm & 0xFFF) << 20) | (reg(rs1) << 15) | (0b111 << 12) | (reg(rd) << 7) | 0b0010011


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
    code = []
    labels = {}

    # Helper to record label positions
    def L(name):
        labels[name] = base + len(code) * 4

    def emit(inst):
        code.append(inst)

    # Program
    emit(lui('t0', 0x70000))
    emit(lui('t6', 0x80002))
    emit(lui('t1', 0x12345))
    emit(addi('t1', 't1', 0x678))
    emit(sw('t1', -32, 't6'))

    # Load weights = 1
    emit(addi('t1', 'zero', 1))
    for off in [0x40, 0x44, 0x48, 0x4C, 0x50, 0x54, 0x58, 0x5C]:
        emit(sw('t1', off, 't0'))

    # Trigger weight load
    emit(addi('t1', 'zero', 2))
    emit(sw('t1', 0, 't0'))

    # Poll weights_loaded
    L('poll_wg')
    emit(lw('t1', 4, 't0'))
    emit(andi('t1', 't1', 2))
    emit(beq('t1', 'zero', labels.setdefault('poll_wg', base + len(code) * 4), base + (len(code) - 1) * 4))
    # Fixup: need two-pass for labels; do simple two-pass below

    # This single-pass approach won't work for backward branches.
    # Rewrite using two-pass assembly.
    raise RuntimeError("Use two_pass function below")


def two_pass():
    base = 0x80000000
    # First pass: collect labels
    labels = {}
    pc = base

    def mark(name):
        labels[name] = pc

    ops = []

    def op(name, *args):
        nonlocal pc
        ops.append((name, args))
        pc += 4

    op('lui', 't0', 0x70000)
    op('lui', 't6', 0x80002)
    op('lui', 't1', 0x12345)
    op('addi', 't1', 't1', 0x678)
    op('sw', 't1', -32, 't6')

    op('addi', 't1', 'zero', 1)
    for off in [0x40, 0x44, 0x48, 0x4C, 0x50, 0x54, 0x58, 0x5C]:
        op('sw', 't1', off, 't0')

    op('addi', 't1', 'zero', 2)
    op('sw', 't1', 0, 't0')

    mark('poll_wg')
    op('lw', 't1', 4, 't0')
    op('andi', 't1', 't1', 2)
    op('beq', 't1', 'zero', 'poll_wg')

    op('lui', 't1', 0x01010)
    op('addi', 't1', 't1', 0x101)
    op('sw', 't1', 0x100, 't0')

    for off in [0x110, 0x114, 0x118, 0x11C, 0x120, 0x124, 0x128, 0x12C]:
        op('sw', 'zero', off, 't0')

    op('addi', 't1', 'zero', 1)
    op('sw', 't1', 0, 't0')

    mark('poll_done')
    op('lw', 't1', 4, 't0')
    op('andi', 't1', 't1', 4)
    op('beq', 't1', 'zero', 'poll_done')

    op('lw', 't4', 0x200, 't0')
    op('addi', 't5', 'zero', 4)
    op('bne', 't4', 't5', 'fail')

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

    # Second pass: encode
    encoders = {
        'lui': lui, 'addi': addi, 'sw': sw, 'lw': lw,
        'andi': andi, 'beq': beq, 'bne': bne, 'j': j
    }

    pc = base
    out = ["// Auto-generated from chipdesign_npu_test.S by gen_hex.py"]
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
    print(two_pass())
