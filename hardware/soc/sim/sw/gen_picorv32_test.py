"""Generate a toolchain-independent RV32IMC/IRQ/byte-access test image."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_hex import REGS, lui, addi, lw, sw, bne, jump


def image():
    code = bytearray()
    labels, fixups = {}, []
    base = 0x80000000

    def emit(value, size=4):
        code.extend((value & ((1 << (8 * size)) - 1)).to_bytes(size, 'little'))

    def label(name):
        labels[name] = base + len(code)

    def branch(a, b, name):
        fixups.append((len(code), 'bne', (a, b, name)))
        emit(0)

    def li(reg, value):
        value &= 0xffffffff
        low = value & 0xfff
        if low & 0x800:
            low -= 0x1000
        emit(lui(reg, ((value - low) >> 12) & 0xfffff))
        emit(addi(reg, reg, low))

    def expect(reg, value):
        li('t4', value)
        branch(reg, 't4', 'fail')

    def alu(rd, rs1, rs2, funct3, funct7=1):
        emit((funct7 << 25) | (REGS[rs2] << 20) | (REGS[rs1] << 15) |
             (funct3 << 12) | (REGS[rd] << 7) | 0x33)

    # Reset program branches around the Pico IRQ vector at SRAM+0x10.
    emit(jump(base + 0x80, base))
    while len(code) < 0x10:
        emit(addi('zero', 'zero', 0))
    # Save temporaries in q2/q3, record the IRQ mask, restore, retirq.
    emit((1 << 25) | (5 << 15) | (2 << 7) | 0x0b)  # setq q2,t0
    emit((1 << 25) | (6 << 15) | (3 << 7) | 0x0b)  # setq q3,t1
    emit((1 << 15) | (5 << 7) | 0x0b)             # getq t0,q1
    emit(lui('t1', 0x80001))
    emit(sw('t0', 0x700, 't1'))
    emit((2 << 15) | (5 << 7) | 0x0b)             # getq t0,q2
    emit((3 << 15) | (6 << 7) | 0x0b)             # getq t1,q3
    emit((2 << 25) | 0x0b)                        # retirq
    while len(code) < 0x80:
        emit(addi('zero', 'zero', 0))

    li('t6', 0x80001000)
    li('t0', -7)
    li('t1', 3)
    for funct3, expected in [(0, -21), (1, -1), (4, -2), (6, -1),
                             (5, 0xfffffff9 // 3), (7, 0xfffffff9 % 3)]:
        alu('t2', 't0', 't1', funct3)
        expect('t2', expected)
    li('t0', 0x80000000)
    li('t1', -1)
    alu('t2', 't0', 't1', 4)
    expect('t2', 0x80000000)  # signed overflow
    alu('t2', 't0', 'zero', 4)
    expect('t2', -1)           # divide by zero
    alu('t2', 't0', 'zero', 6)
    expect('t2', 0x80000000)

    emit(sw('zero', 0x600, 't6'))
    li('t0', 0x80)
    emit((5 << 20) | (31 << 15) | (0x601 << 7 & 0xf80) |
         ((0x601 >> 5) << 25) | 0x23)             # sb t0,0x601(t6)
    # General load encodings use the requested byte offset.
    for funct3, expected in [(0, -128), (4, 128)]:
        emit((0x601 << 20) | (31 << 15) | (funct3 << 12) | (7 << 7) | 3)
        expect('t2', expected)
    li('t0', 0x9234)
    emit(((0x602 >> 5) << 25) | (5 << 20) | (31 << 15) | (1 << 12) |
         ((0x602 & 31) << 7) | 0x23)              # sh t0,0x602(t6)
    for funct3, expected in [(1, -28108), (5, 0x9234), (2, 0x92348000)]:
        offset = 0x600 if funct3 == 2 else 0x602
        emit((offset << 20) | (31 << 15) | (funct3 << 12) | (7 << 7) | 3)
        expect('t2', expected)

    emit(0x441d, 2)                              # c.li s0,7
    emit(addi('s0', 's0', -2))                    # 32-bit at a halfword boundary
    expect('s0', 5)
    emit(0x0001, 2)                              # c.nop; restore word alignment
    emit(0xc0002373)                             # rdcycle t1
    emit(addi('zero', 'zero', 0))
    emit(0xc00023f3)                             # rdcycle t2
    # sltu t3,t1,t2: counters must advance.
    alu('t3', 't1', 't2', 3, 0)
    expect('t3', 1)

    li('t0', 0xfffcffff)
    emit((3 << 25) | (5 << 15) | 0x0b)           # unmask IRQ16 and IRQ17
    for stage, mask in [(1, 0x10000), (2, 0x20000)]:
        label(f'irq{stage}')
        emit(lw('t0', 0x700, 't6'))
        li('t1', mask)
        branch('t0', 't1', f'irq{stage}')
        emit(addi('t0', 'zero', stage))
        emit(sw('t0', 0x704, 't6'))

    li('t0', 0xc0dec0de)
    emit(sw('t0', 0x7e0, 't6'))
    label('done')
    emit(jump(labels['done'], labels['done']))
    label('fail')
    li('t0', 0xdeadbeef)
    emit(sw('t0', 0x7e0, 't6'))
    emit(jump(labels['done'], base + len(code)))
    for offset, kind, args in fixups:
        a, b, target = args
        code[offset:offset+4] = bne(a, b, labels[target], base + offset).to_bytes(4, 'little')
    code.extend(bytes((-len(code)) % 4))
    return ''.join(f'{int.from_bytes(code[i:i+4], "little"):08x}\n'
                   for i in range(0, len(code), 4))


if __name__ == '__main__':
    destination = Path(__file__).with_name('picorv32_isa_test.hex')
    destination.write_text(image(), encoding='ascii')
    print(destination)
