"""Compile the largest >=2-frame KWS workload that fits the 8 KiB SRAM."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np
from kws_workload_common import ROOT, build_directory, load_inputs, load_package


def initializer(array):
    array = np.asarray(array)
    if array.ndim == 0:
        return str(int(array))
    return '{' + ','.join(initializer(a) for a in array) + '}'


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--package', required=True)
    p.add_argument('--inputs', required=True, help='INT8 .npy or export_rtl_vectors inputs.hex')
    p.add_argument('--output', default='build/kws_workload')
    p.add_argument('--use-dma', type=int, choices=(0, 1), default=1,
                   help='KWS_USE_DMA compile switch (default: 1)')
    p.add_argument('--tool-prefix', default=r'D:\tools\xpack-riscv-none-elf-gcc-15.2.0-1\bin\riscv-none-elf-')
    args = p.parse_args()
    pkg, inputs = load_package(args.package), load_inputs(args.inputs)
    out = build_directory(args.output)
    if len(inputs) < 2:
        raise ValueError('at least two inputs are required')
    fw = ROOT / 'software/firmware/kws_baseline'
    words = np.frombuffer(pkg['weight_mem'][:241].astype(np.int8).tobytes(), dtype='<u4').reshape(241, 2)
    params = []
    for prefix in ('conv1', 'conv2', 'fc'):
        bias = np.zeros(8, dtype=np.int32)
        bias[:len(pkg[prefix + '_bias'])] = pkg[prefix + '_bias']
        params.append('{' + ','.join([initializer(bias),
            initializer(pkg[prefix + '_mult'] if prefix != 'fc' else np.zeros(8, dtype=np.int32)),
            initializer(pkg[prefix + '_shift'] if prefix != 'fc' else np.zeros(8, dtype=np.int8))]) + '}')
    model = '{' + ','.join([initializer(words), '{' + ','.join(params) + '}',
                           initializer(pkg['fc_cmp_mult']), initializer(pkg['fc_cmp_shift'])]) + '}'
    def run(tool, arguments, check=True):
        result = subprocess.run([args.tool_prefix + tool, *map(str, arguments)],
                                capture_output=True, text=True)
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result
    # Even an empty program cannot fit more frames, results and stack than this.
    ceiling = (0x1fe0 - 256 - 16) // (320 + 40)
    attempts = []
    for count in range(min(len(inputs), ceiling), 1, -1):
        (out / 'workload_data.h').write_text(
            f'#include "kws.h"\n#define SAMPLE_COUNT {count}\n'
            'extern const kws_model workload_model;\n'
            'extern const int8_t workload_inputs[SAMPLE_COUNT][320];\n')
        (out / 'workload_data.c').write_text('#include "workload_data.h"\n'
            f'const kws_model workload_model = {model};\n'
            f'const int8_t workload_inputs[SAMPLE_COUNT][320] = {initializer(inputs[:count])};\n')
        result = run('gcc', ['-march=rv32imc', '-mabi=ilp32', '-Os', '-g',
            '-ffreestanding', '-fno-builtin', '-fno-tree-loop-distribute-patterns',
            '-msmall-data-limit=0', '-mno-relax', '-fstack-usage', '-nostdlib', '-static',
            '-Wall', '-Wextra', '-Werror', '-DKWS_USE_DMA=' + str(args.use_dma),
            '-I' + str(ROOT / 'software/drivers'),
            '-I' + str(ROOT / 'software/runtime'), '-I' + str(out),
            fw / 'startup.S', fw / 'main.c', ROOT / 'software/runtime/kws.c', out / 'workload_data.c',
            '-T' + str(fw / 'linker.ld'), '-Wl,--defsym,SAMPLE_COUNT=' + str(count),
            '-Wl,-Map=' + str(out / 'workload.map'), '-o', out / 'workload.elf', '-lgcc'], check=False)
        attempts.append({'count': count, 'exit': result.returncode, 'diagnostics': result.stderr})
        if result.returncode == 0:
            break
        if 'image overlaps results/stack' not in result.stderr:
            raise RuntimeError(result.stdout + result.stderr)
    else:
        raise RuntimeError('cannot fit two frames in SRAM')
    (out / 'fit_attempts.json').write_text(json.dumps(attempts, indent=2))
    run('objcopy', ['-O', 'binary', out / 'workload.elf', out / 'workload.bin'])
    dump = run('objdump', ['-d', '-S', out / 'workload.elf']).stdout
    (out / 'workload.dump').write_text(dump)
    size = run('size', ['-A', out / 'workload.elf']).stdout
    (out / 'size.txt').write_text(size)
    nm = run('nm', ['-n', out / 'workload.elf']).stdout
    (out / 'symbols.txt').write_text(nm)
    symbols = {parts[2]: int(parts[0], 16) for line in nm.splitlines()
               if len(parts := line.split()) == 3}
    image = (out / 'workload.bin').read_bytes()
    if len(image) > symbols['__results'] - 0x80000000:
        raise ValueError('binary overlaps results')
    image += bytes(8192 - len(image))
    (out / 'workload.hex').write_text(''.join(f'{int.from_bytes(image[i:i+4], "little"):08x}\n'
                                            for i in range(0, 8192, 4)), encoding='ascii')
    np.save(out / 'inputs.npy', inputs[:count])
    np.savez(out / 'package.npz', **pkg)
    manifest = dict(schema=1, count=count, available_inputs=len(inputs), use_dma=args.use_dma,
        image_bytes=len((out / 'workload.bin').read_bytes()),
        image_end=hex(symbols['__image_end']), results_address=hex(symbols['__results']),
        results_bytes=16 + 40 * count, stack_bytes=256, stack_bottom=hex(symbols['__stack_bottom']),
        stack_top=hex(symbols['__stack_top']), magic_address='0x80001fe0',
        model_address=hex(symbols['workload_model']), inputs_address=hex(symbols['workload_inputs']),
        toolchain=run('gcc', ['--version']).stdout.splitlines()[0],
        sha256={name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                for name in ['package.npz', 'inputs.npy', 'workload.hex']})
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(size)
    print(json.dumps(manifest, indent=2))
    print('Maximum fitted count from available inputs:', count)


if __name__ == '__main__':
    main()
