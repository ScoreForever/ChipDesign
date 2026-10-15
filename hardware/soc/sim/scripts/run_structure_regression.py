#!/usr/bin/env python3
"""Run the integrated SoC regression with Xcelium on Linux."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[4]
CASES = [
    ('soc', 'chipdesign_soc_tb', 'PASS: integrated SoC test OK', []),
    ('dma', 'chipdesign_dma_tb', 'PASS: NPU DMA test OK', []),
    ('requant', 'tb_requant_unit', 'ALL REQUANT UNIT TESTS PASSED', []),
    ('capacity_asymmetric', 'tb_tinycnn8_capacity', 'ALL FLAT CAPACITY TESTS PASSED', []),
    ('capacity_generic', 'tb_tinycnn8_capacity', 'ALL FLAT CAPACITY TESTS PASSED',
     ['-gpg', 'tb_tinycnn8_capacity.ACT_BANK_BYTES=>4096', '-gpg', 'tb_tinycnn8_capacity.ACT_A_BANK_BYTES=>4096']),
    ('sram_pipeline', 'tb_axi_sram_pipeline', 'ALL AXI SRAM PIPELINE TESTS PASSED', []),
    ('sram_legacy', 'tb_axi_sram_pipeline', 'ALL AXI SRAM PIPELINE TESTS PASSED',
     ['-gpg', 'tb_axi_sram_pipeline.READ_LATENCY=>1']),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', choices=('asic', 'fpga'), default='asic')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error('--output must be empty to preserve prior evidence')
    (ROOT / 'hardware/soc/sim/out').mkdir(parents=True, exist_ok=True)
    summary = {'tool': 'xrun', 'config': args.config, 'cases': [], 'passed': False}
    for name, top, marker, generics in CASES:
        command = ['xrun', '-64bit', '-sv', '-timescale', '1ns/1ps',
                   '-f', 'hardware/soc/sim/filelists/chipdesign_soc.f',
                   'hardware/npu/tb/tb_requant_unit.sv',
                   'hardware/npu/tb/tb_tinycnn8_capacity.sv',
                   'hardware/soc/sim/tb/tb_axi_sram_pipeline.sv',
                   '-top', top, '-xmlibdirname', str(output / (name + '_db')),
                   '-l', str(output / (name + '.log')), *generics]
        if args.config == 'fpga':
            command.append('+define+CHIPDESIGN_FPGA_MEM')
        with (output / (name + '_console.log')).open('w') as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        content = (output / (name + '_console.log')).read_text()
        passed = result.returncode == 0 and marker in content and not re.search(
            r'\*(?:E|F),|\bFAIL:|\bTIMEOUT|Assertion.*failed', content, re.I)
        summary['cases'].append({'name': name, 'top': top, 'passed': passed,
                                 'returncode': result.returncode,
                                 'pass_lines': [line for line in content.splitlines() if marker in line],
                                 'command': command})
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(f'{"PASS" if passed else "FAIL"} {name}', flush=True)
        if not passed:
            print(content[-5000:])
            raise SystemExit(1)
    summary['passed'] = True
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
