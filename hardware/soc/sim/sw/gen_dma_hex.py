#!/usr/bin/env python3
"""Generate chipdesign_dma_test.hex — NPU DMA self-checking test.

Purpose
-------
Exercise the SoC AXI DMA end to end and verify the copied bytes, without
depending on the old Matrix-Unit MMIO semantics (start / weights_loaded /
out_valid), which the TinyCNN-8 integration replaced.

Flow
----
1. Write 8 known words (0x01010101) into SRAM at 0x80001000.
2. Program the DMA: SRC=0x80001000, DST=0x80001800, LEN=8.
3. Start it, then poll DMA_STATUS (0x70000410) until the done bit is set.
4. Read all 8 words back from 0x80001800 and compare against the pattern.
5. Write PASS / FAIL to the magic word at 0x80001FE0.

Two pitfalls this generator exists to avoid
-------------------------------------------
* **Never point the DMA at its own configuration aperture.** The destination
  must not fall in 0x70000400..0x7000041C, because that window contains the
  engine's SRC/DST/LEN/CTRL registers and the copy overwrites them mid-flight.
  Doing so corrupts LEN into 0x01010101 and the engine then counts down from
  ~16.8M words, so it never finishes and the poll never exits.  This was a real
  defect in an earlier revision of this test.  SRAM-to-SRAM keeps the engine's
  registers untouched, and the CPU can read the destination back to prove the
  transfer landed.
* **Write the file from Python, not via a shell redirect.** PowerShell's ">"
  emits UTF-16LE with a BOM, which $readmemh cannot parse; the SRAM array then
  silently stays all-x and the program never appears to run at all.

The destination was 0x70000040 (MATRIX_WEIGHT[0]) before the TinyCNN-8
integration; that register no longer exists in the current wrapper.

Assembler
---------
Self-contained two-pass RV32I subset: lui, addi, lw, sw, andi, beq, bne, j.
Labels are resolved after all addresses are known, so forward branches are
exact and no fixup pass is needed.
"""

import os
import sys

REGS = {
    'zero': 0,  'ra': 1,   'sp': 2,   'gp': 3,
    'tp': 4,    't0': 5,   't1': 6,   't2': 7,
    's0': 8,    's1': 9,   'a0': 10,  'a1': 11,
    'a2': 12,   'a3': 13,  'a4': 14,  'a5': 15,
    'a6': 16,   'a7': 17,  's2': 18,  's3': 19,
    's4': 20,   's5': 21,  's6': 22,  's7': 23,
    's8': 24,   's9': 25,  's10': 26, 's11': 27,
    't3': 28,   't4': 29,  't5': 30,  't6': 31,
}

BASE = 0x80000000
MAGIC_ADDR = 0x80001FE0
SRC_ADDR = 0x80001000          # pattern source
DST_ADDR = 0x80001800          # DMA destination, verified by the CPU
NPU_BASE = 0x70000000
DMA_LEN = 8
PATTERN = 0x01010101


def reg(r):
    if r not in REGS:
        raise ValueError("unknown register %r" % r)
    return REGS[r]


def _u12(v):
    v &= 0xFFF
    if v & 0x800:
        v -= 0x1000
    return v


def _s12(v):
    if not -2048 <= v <= 2047:
        raise ValueError("12-bit signed immediate out of range: %d" % v)
    return v & 0xFFF


def enc_lui(rd, imm20):
    return ((imm20 & 0xFFFFF) << 12) | (reg(rd) << 7) | 0b0110111


def enc_addi(rd, rs1, imm):
    return (_s12(imm) << 20) | (reg(rs1) << 15) | (reg(rd) << 7) | 0b0010011


def enc_lw(rd, imm, rs1):
    return (_s12(imm) << 20) | (reg(rs1) << 15) | (0b010 << 12) | (reg(rd) << 7) | 0b0000011


def enc_sw(rs2, imm, rs1):
    i = _s12(imm)
    return (((i >> 5) & 0x7F) << 25) | (reg(rs2) << 20) | (reg(rs1) << 15) \
        | (0b010 << 12) | ((i & 0x1F) << 7) | 0b0100011


def enc_andi(rd, rs1, imm):
    return (_s12(imm) << 20) | (reg(rs1) << 15) | (0b111 << 12) | (reg(rd) << 7) | 0b0010011


def enc_branch(rs1, rs2, offset, funct3):
    if not -4096 <= offset <= 4094 or offset % 2:
        raise ValueError("branch offset out of range or odd: %d" % offset)
    imm = offset & 0x1FFF
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) \
        | (reg(rs2) << 20) | (reg(rs1) << 15) | (funct3 << 12) \
        | (((imm >> 1) & 0xF) << 8) | (((imm >> 11) & 1) << 7) | 0b1100011


def enc_j(offset):
    if not -1048576 <= offset <= 1048574 or offset % 2:
        raise ValueError("jump offset out of range or odd: %d" % offset)
    imm = offset & 0x1FFFFF
    return (((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) \
        | (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12) | 0b1101111


def enc_ori(rd, rs1, imm):
    """Kept for future test programs; currently unused."""
    return (_s12(imm) << 20) | (reg(rs1) << 15) | (0b110 << 12) | (reg(rd) << 7) | 0b0010011


def assemble(program):
    """program: list of (mnemonic, *args). Labels are ('label', name).

    Returns (words, labels). Addresses are assigned first, then every
    reference is encoded against the finished map, so forward branches and
    jumps need no fixups.
    """
    labels = {}
    pc = BASE
    for item in program:
        if item[0] == 'label':
            labels[item[1]] = pc
        else:
            pc += 4

    words = []
    pc = BASE
    for item in program:
        if item[0] == 'label':
            continue
        name, args, at = item[0], item[1:], pc
        if name == 'beq':
            w = enc_branch(args[0], args[1], labels[args[2]] - at, 0b000)
        elif name == 'bne':
            w = enc_branch(args[0], args[1], labels[args[2]] - at, 0b001)
        elif name == 'j':
            w = enc_j(labels[args[0]] - at)
        else:
            w = {
                'lui': enc_lui, 'addi': enc_addi, 'lw': enc_lw,
                'sw': enc_sw, 'andi': enc_andi,
            }[name](*args)
        words.append(w & 0xFFFFFFFF)
        pc += 4
    return words, labels


def program():
    p = []
    p.append(('label', '_start'))

    # t6 = 0x80002000, and the magic word sits 32 bytes below it.
    MAGIC_OFF = MAGIC_ADDR - 0x80002000          # == -32
    p.append(('lui', 't6', 0x80002000 >> 12))
    p.append(('lui', 't1', 0x12345))
    p.append(('addi', 't1', 't1', 0x678))
    p.append(('sw', 't1', MAGIC_OFF, 't6'))

    # fill SRAM scratch with the pattern
    p.append(('lui', 't5', SRC_ADDR >> 12))
    p.append(('lui', 't1', PATTERN >> 12))
    p.append(('addi', 't1', 't1', PATTERN & 0xFFF))
    for off in range(0, DMA_LEN * 4, 4):
        p.append(('sw', 't1', off, 't5'))

    # NPU base (DMA configuration aperture)
    p.append(('lui', 't0', NPU_BASE >> 12))

    # program DMA: SRC and DST are both plain SRAM, so the transfer cannot
    # disturb the engine's own configuration registers (see the module
    # docstring for why an MMIO destination is unsafe).
    p.append(('lui', 't1', SRC_ADDR >> 12))
    p.append(('sw', 't1', 0x400, 't0'))
    p.append(('lui', 't1', DST_ADDR >> 12))
    p.append(('sw', 't1', 0x404, 't0'))
    p.append(('addi', 't1', 'zero', DMA_LEN))
    p.append(('sw', 't1', 0x408, 't0'))

    # start the copy (bit0 only: no IRQ)
    p.append(('addi', 't1', 'zero', 1))
    p.append(('sw', 't1', 0x40C, 't0'))

    # poll DMA_STATUS bit1 (done)
    p.append(('label', 'poll_dma'))
    p.append(('lw', 't1', 0x410, 't0'))
    p.append(('andi', 't1', 't1', 2))
    p.append(('beq', 't1', 'zero', 'poll_dma'))

    # verify: the destination must now hold the pattern
    p.append(('lui', 't3', PATTERN >> 12))
    p.append(('addi', 't3', 't3', PATTERN & 0xFFF))
    p.append(('lui', 't4', DST_ADDR >> 12))
    p.append(('addi', 't2', 'zero', DMA_LEN))
    p.append(('label', 'chk'))
    p.append(('lw', 't5', 0, 't4'))
    p.append(('bne', 't5', 't3', 'fail'))
    p.append(('addi', 't4', 't4', 4))
    p.append(('addi', 't2', 't2', -1))
    p.append(('bne', 't2', 'zero', 'chk'))

    # PASS
    p.append(('lui', 't1', 0xC0DEC))
    p.append(('addi', 't1', 't1', 0x0DE))
    p.append(('sw', 't1', -32, 't6'))
    p.append(('label', 'done'))
    p.append(('j', 'done'))

    # FAIL
    p.append(('label', 'fail'))
    p.append(('lui', 't1', 0xDEADC))
    p.append(('addi', 't1', 't1', -0x111))
    p.append(('sw', 't1', -32, 't6'))
    p.append(('j', 'done'))

    return p


def main():
    words, labels = assemble(program())
    out = [
        "// Auto-generated by hardware/soc/sim/sw/gen_dma_hex.py",
        "// NPU AXI DMA self-checking test (TinyCNN-8 register map).",
        "// " + ", ".join("%s=0x%08x" % (k, v) for k, v in sorted(labels.items())),
    ]
    out += ["%08x" % w for w in words]
    text = "\n".join(out) + "\n"

    # Write the file here rather than letting a shell redirect it: PowerShell's
    # ">" emits UTF-16LE with a BOM, which $readmemh cannot parse (the array
    # silently stays all-x).  Always plain ASCII/UTF-8 without a BOM.
    if len(sys.argv) > 1 and sys.argv[1] not in ('-', '--stdout'):
        path = sys.argv[1]
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "chipdesign_dma_test.hex")
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write(text)
    sys.stderr.write("wrote %s (%d instructions, %d bytes)\n"
                     % (path, len(words), len(text)))
    return text


if __name__ == '__main__':
    main()
