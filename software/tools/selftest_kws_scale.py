"""Compare host C or interpreted RV32 ELF scale code with quantize.py.

The ELF mode executes objdump instructions with RV32 register semantics;
it checks the target mul/mulh path, but does not simulate SoC timing.
"""
import argparse
import ctypes
import random
import re
import subprocess
from quantize import multiply_by_quantized_multiplier


def signed(value):
    value &= 0xffffffff
    return value - 2**32 if value & 0x80000000 else value


class RV32:
    """Small, fail-closed interpreter for the integer instructions in kws_scale."""

    def __init__(self, elf, objdump):
        dump = subprocess.run([objdump, '-d', elf], check=True,
                              capture_output=True, text=True).stdout
        self.code, self.symbols = {}, {}
        for line in dump.splitlines():
            symbol = re.match(r'^([0-9a-f]+) <([^>]+)>:', line)
            if symbol:
                self.symbols[symbol[2]] = int(symbol[1], 16)
            insn = re.match(r'^\s*([0-9a-f]+):\s+([0-9a-f]+)\s+(\S+)\s*(.*)', line)
            if insn:
                operands = insn[4].split('#')[0].split('<')[0].strip()
                self.code[int(insn[1], 16)] = (len(insn[2]) // 2, insn[3],
                                             operands.split(',') if operands else [])

    def run(self, symbol, arguments, memory=None):
        regs = dict(zero=0, ra=0, sp=0x90001000)
        regs.update({f'a{i}': v & 0xffffffff for i, v in enumerate(arguments)})
        memory = {} if memory is None else memory
        pc = self.symbols[symbol]
        count = 0
        while pc:
            count += 1
            if count > 10000:
                raise AssertionError('instruction limit exceeded')
            size, op, args = self.code[pc]
            next_pc = pc + size
            def reg(name):
                return regs.get(name, 0)
            def put(value):
                if args[0] != 'zero':
                    regs[args[0]] = value & 0xffffffff
            def address(operand):
                match = re.fullmatch(r'(-?\d+)\((\w+)\)', operand)
                assert match, operand
                return (reg(match[2]) + int(match[1])) & 0xffffffff
            if op in ('li', 'lui'):
                put(int(args[1], 0) << (12 if op == 'lui' else 0))
            elif op == 'auipc':
                put(pc + (int(args[1], 0) << 12))
            elif op == 'mv':
                put(reg(args[1]))
            elif op == 'neg':
                put(-reg(args[1]))
            elif op == 'seqz':
                put(int(reg(args[1]) == 0))
            elif op == 'zext.b':
                put(reg(args[1]) & 255)
            elif op in ('add', 'addi', 'sub', 'and', 'andi', 'or', 'sltu',
                        'sll', 'slli', 'srl', 'srli', 'sra', 'srai', 'mul', 'mulh'):
                left = reg(args[1])
                right = int(args[2], 0) if op.endswith('i') else reg(args[2])
                base = op[:-1] if op.endswith('i') else op
                if base == 'add': result = left + right
                elif base == 'sub': result = left - right
                elif base == 'and': result = left & right
                elif base == 'or': result = left | right
                elif base == 'sltu': result = int(left < right)
                elif base == 'sll': result = left << (right & 31)
                elif base == 'srl': result = left >> (right & 31)
                elif base == 'sra': result = signed(left) >> (right & 31)
                elif base == 'mul': result = left * right
                elif base == 'mulh': result = (signed(left) * signed(right)) >> 32
                put(result)
            elif op in ('lw', 'lb', 'lbu'):
                addr = address(args[1])
                width = 4 if op == 'lw' else 1
                assert width == 1 or addr % 4 == 0, hex(addr)
                value = sum(memory[addr + i] << (8 * i) for i in range(width))
                put(value - 256 if op == 'lb' and value >= 128 else value)
            elif op == 'sw':
                addr = address(args[1])
                assert addr % 4 == 0, hex(addr)
                for i in range(4):
                    memory[addr + i] = (reg(args[0]) >> (8 * i)) & 255
            elif op in ('beq', 'bne', 'blt', 'bge', 'bltu', 'bgeu'):
                left, right = reg(args[0]), reg(args[1])
                if op in ('blt', 'bge'):
                    left, right = signed(left), signed(right)
                take = {'beq': left == right, 'bne': left != right,
                        'blt': left < right, 'bge': left >= right,
                        'bltu': left < right, 'bgeu': left >= right}[op]
                if take: next_pc = int(args[2], 16)
            elif op in ('beqz', 'bnez', 'blez', 'bgez', 'bltz', 'bgtz'):
                value = signed(reg(args[0]))
                take = {'beqz': value == 0, 'bnez': value != 0,
                        'blez': value <= 0, 'bgez': value >= 0,
                        'bltz': value < 0, 'bgtz': value > 0}[op]
                if take: next_pc = int(args[1], 16)
            elif op == 'j':
                next_pc = int(args[0], 16)
            elif op in ('jalr', 'jr'):
                next_pc = address(args[-1]) & ~1
                if op == 'jalr': regs['ra'] = pc + size
            elif op == 'ret':
                next_pc = reg('ra')
            else:
                raise AssertionError(f'unsupported instruction {op} at {pc:x}')
            pc = next_pc
        return signed(reg('a0')), count


def main():
    parser = argparse.ArgumentParser(__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--library', help='host shared library built from runtime/kws.c')
    target.add_argument('--elf', help='RV32 workload ELF; interpret the actual target code')
    parser.add_argument('--objdump', default=r'D:\tools\xpack-riscv-none-elf-gcc-15.2.0-1\bin\riscv-none-elf-objdump.exe')
    parser.add_argument('--reference-elf', help='also compare against the original RV32 implementation')
    parser.add_argument('--random-count', type=int, default=100000)
    args = parser.parse_args()
    if args.elf:
        machine = RV32(args.elf, args.objdump)
        scale = lambda v, m, s: machine.run('kws_scale', (v, m, s))[0]
    else:
        library = ctypes.CDLL(args.library)
        scale = library.kws_scale
        scale.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_int]
        scale.restype = ctypes.c_int32
    original = RV32(args.reference_elf, args.objdump) if args.reference_elf else None
    values = [-2**31, -2**31 + 1, -65537, -3, -2, -1, 0, 1, 2, 3, 65537, 2**31 - 1]
    multipliers = [-2**31, -2**31+1, -1, 0, 1, 2**30, 2**31 - 1]
    cases = [(v, m, s) for v in values for m in multipliers for s in range(-31, 32)]
    # Values just inside/outside every saturating-left-shift boundary.
    cases += [(v, m, s) for s in range(1, 32)
              for edge in ((2**31 - 1) >> s, -(2**31) >> s)
              for v in (edge - 1, edge, edge + 1) for m in multipliers]
    # Products immediately around Q31 positive and negative half-way ties.
    cases += [(v, m, s) for v in (-3, -1, 1, 3)
              for m in (2**30 - 1, 2**30, 2**30 + 1, -2**30 - 1, -2**30, -2**30 + 1)
              for s in range(-31, 32)]
    rng = random.Random(17)
    cases += [(rng.randrange(-2**31, 2**31), rng.randrange(-2**31, 2**31), i % 63 - 31)
              for i in range(args.random_count)]
    for value, multiplier, shift in cases:
        got = scale(value, multiplier, shift)
        expected = multiply_by_quantized_multiplier(value, multiplier, shift)
        assert got == expected, (value, multiplier, shift, got, expected)
        if original:
            old, _ = original.run('kws_scale', (value, multiplier, shift))
            assert got == old, (value, multiplier, shift, got, old)
    mode = 'interpreted RV32/Python' if args.elf else 'native C/Python'
    print(f'PASS: {len(cases)} {mode} scale comparisons, shifts -31..31 and INT32 extremes'
          + ('; original RV32 also bit-exact' if original else ''))


if __name__ == '__main__':
    main()
