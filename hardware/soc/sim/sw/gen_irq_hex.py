#!/usr/bin/env python3
"""Generate chipdesign_npu_irq_test.hex — NPU interrupt test program.

The program is loaded at SRAM_BASE (0x80000000).  _start doubles as the
mtvec trap-vector base (direct mode).  On reset mcause is 0, so execution
falls through to main.  When the NPU interrupt (irq_i[12]) fires, mcause
has bit 31 set and we branch to the trap handler.
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

CSRS = {
    'mstatus': 0x300,
    'mie': 0x304,
    'mtvec': 0x305,
    'mcause': 0x342,
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


def blt(rs1, rs2, label_pc, cur_pc):
    imm = label_pc - cur_pc
    imm12 = (imm >> 12) & 1
    imm10_5 = (imm >> 5) & 0x3F
    imm4_1 = (imm >> 1) & 0xF
    imm11 = (imm >> 11) & 1
    return (imm12 << 31) | (imm10_5 << 25) | (reg(rs2) << 20) | (reg(rs1) << 15) | (0b100 << 12) | (imm4_1 << 8) | (imm11 << 7) | 0b1100011


def bne(rs1, rs2, label_pc, cur_pc):
    imm = label_pc - cur_pc
    imm12 = (imm >> 12) & 1
    imm10_5 = (imm >> 5) & 0x3F
    imm4_1 = (imm >> 1) & 0xF
    imm11 = (imm >> 11) & 1
    return (imm12 << 31) | (imm10_5 << 25) | (reg(rs2) << 20) | (reg(rs1) << 15) | (0b001 << 12) | (imm4_1 << 8) | (imm11 << 7) | 0b1100011


def beq(rs1, rs2, label_pc, cur_pc):
    imm = label_pc - cur_pc
    imm12 = (imm >> 12) & 1
    imm10_5 = (imm >> 5) & 0x3F
    imm4_1 = (imm >> 1) & 0xF
    imm11 = (imm >> 11) & 1
    return (imm12 << 31) | (imm10_5 << 25) | (reg(rs2) << 20) | (reg(rs1) << 15) | (0b000 << 12) | (imm4_1 << 8) | (imm11 << 7) | 0b1100011


def j(label_pc, cur_pc):
    imm = label_pc - cur_pc
    imm20 = (imm >> 20) & 1
    imm10_1 = (imm >> 1) & 0x3FF
    imm11 = (imm >> 11) & 1
    imm19_12 = (imm >> 12) & 0xFF
    return (imm20 << 31) | (imm10_1 << 21) | (imm11 << 20) | (imm19_12 << 12) | (0 << 7) | 0b1101111


def csrrs(rd, csr, rs1):
    csr_addr = CSRS[csr] if isinstance(csr, str) else csr
    return (csr_addr << 20) | (reg(rs1) << 15) | (0b010 << 12) | (reg(rd) << 7) | 0b1110011


def csrrw(rd, csr, rs1):
    csr_addr = CSRS[csr] if isinstance(csr, str) else csr
    return (csr_addr << 20) | (reg(rs1) << 15) | (0b001 << 12) | (reg(rd) << 7) | 0b1110011


def csrrsi(rd, csr, imm):
    csr_addr = CSRS[csr] if isinstance(csr, str) else csr
    return (csr_addr << 20) | ((imm & 0x1F) << 15) | (0b110 << 12) | (reg(rd) << 7) | 0b1110011


def wfi():
    return 0x10500073


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

    # _start is both reset entry and mtvec base.
    mark('_start')
    op('csrrs', 't0', 'mcause', 'zero')      # t0 = mcause
    op('blt', 't0', 'zero', 'trap_handler')  # branch if interrupt (msb=1)

    # main: set mtvec, enable interrupts, run NPU, then WFI
    mark('main')
    op('lui', 't0', 0x80000)                 # t0 = 0x80000000 (_start)
    op('csrrw', 'zero', 'mtvec', 't0')       # mtvec = _start
    op('csrrsi', 'zero', 'mstatus', 0x8)     # mstatus.MIE = 1
    op('lui', 't0', 0x00010)                 # t0 = 0x10000 = 1 << 16
    op('csrrs', 'zero', 'mie', 't0')         # mie[16] = 1 (cv32e40p masks 12..15)

    # Write RUNNING
    op('lui', 't6', 0x80002)
    op('lui', 't1', 0x12345)
    op('addi', 't1', 't1', 0x678)
    op('sw', 't1', -32, 't6')

    # NPU base
    op('lui', 't0', 0x70000)

    # Load weights = 1
    op('addi', 't1', 'zero', 1)
    for off in [0x40, 0x44, 0x48, 0x4C, 0x50, 0x54, 0x58, 0x5C]:
        op('sw', 't1', off, 't0')

    # Trigger weight load
    op('addi', 't1', 'zero', 2)
    op('sw', 't1', 0, 't0')

    # Poll until weights_loaded
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

    # Wait for interrupt
    mark('wfi_loop')
    op('wfi')
    op('j', 'wfi_loop')

    # Trap handler: verify result and write PASS/FAIL
    mark('trap_handler')
    op('lw', 't4', 0x200, 't0')              # OUT[0]
    op('addi', 't5', 'zero', 4)              # expected
    op('bne', 't4', 't5', 'fail')

    op('lui', 't1', 0xC0DEC)
    op('addi', 't1', 't1', 0x0DE)            # PASS
    op('sw', 't1', -32, 't6')

    mark('done')
    op('j', 'done')

    mark('fail')
    op('lui', 't1', 0xDEADC)
    op('addi', 't1', 't1', -0x111)           # FAIL
    op('sw', 't1', -32, 't6')
    op('j', 'done')

    encoders = {
        'lui': lui, 'addi': addi, 'sw': sw, 'lw': lw,
        'andi': andi, 'beq': beq, 'bne': bne, 'blt': blt,
        'j': j, 'csrrs': csrrs, 'csrrw': csrrw, 'csrrsi': csrrsi,
        'wfi': wfi
    }

    pc = base
    out = ["// Auto-generated from chipdesign_npu_irq_test.S by gen_irq_hex.py"]
    out.append(f"// labels: { {k: hex(v) for k, v in labels.items()} }")
    for name, args in ops:
        if name in ('beq', 'bne', 'blt'):
            inst = encoders[name](args[0], args[1], labels[args[2]], pc)
        elif name == 'j':
            inst = encoders[name](labels[args[0]], pc)
        elif name == 'wfi':
            inst = encoders[name]()
        else:
            inst = encoders[name](*args)
        out.append(f"{inst:08x}")
        pc += 4

    return '\n'.join(out) + '\n'


if __name__ == '__main__':
    print(main())
