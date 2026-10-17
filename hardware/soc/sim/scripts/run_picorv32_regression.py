"""Run isolated ModelSim CPU/SoC regressions, including Unicode Windows paths.

The simulator works in an ASCII temporary snapshot, never removes the user's
work/ directory, and copies all diagnostics back to --output before exit.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', default='work/picorv32_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    parser.add_argument('--config', choices=['asic', 'fpga', 'both'], default='both')
    parser.add_argument('--workload', action='append', default=[], help='built KWS workload directory; may repeat')
    parser.add_argument('--profile-workload', help='optional CPU-only workload for bus-probe verification')
    parser.add_argument('--profile-only', action='store_true', help='run only the optional bus-probe check')
    parser.add_argument('--test', action='append', help='run only a named test top; may repeat')
    args = parser.parse_args()
    if args.profile_only and not args.profile_workload:
        parser.error('--profile-only requires --profile-workload')
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT) or output == ROOT or output.exists():
        raise ValueError('--output must be a new directory inside the repository')
    output.mkdir(parents=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='chipdesign-pico-') as temporary:
        snapshot = Path(temporary)

        def copy(relative):
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

        filelist = 'hardware/soc/sim/filelists/chipdesign_soc.f'
        copy(filelist)
        for line in (ROOT / filelist).read_text().splitlines():
            if line and not line.startswith('+'):
                copy(line)
        for directory in ['hardware/soc/rtl/include', 'hardware/soc/sim/sw']:
            shutil.copytree(ROOT / directory, snapshot / directory, dirs_exist_ok=True)
        extras = ['hardware/npu/tb/tb_requant_unit.sv',
                  'hardware/npu/tb/tb_tinycnn8_capacity.sv',
                  'hardware/soc/sim/tb/tb_axi_sram_pipeline.sv',
                  'hardware/soc/sim/tb/tb_picorv32_soc.sv',
                  'hardware/soc/sim/tb/kws_workload_tb.sv',
                  'hardware/soc/sim/tb/kws_access_profile_tb.sv',
                  'hardware/soc/sim/tb/kws_saif_tb.sv']
        for relative in extras:
            copy(relative)
        (snapshot / 'hardware/soc/sim/out').mkdir(parents=True)

        def run(command, log, marker=None):
            result = subprocess.run(command, cwd=snapshot, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors='replace', timeout=600)
            text = result.stdout
            (output / log).write_text(text, encoding='utf-8')
            failed = result.returncode != 0 or (marker and marker not in text)
            if marker:
                import re
                failed |= bool(re.search(r'\*\* (?:Fatal|Error):|\bFAIL:|\bTIMEOUT|Errors: [1-9]', text))
            if failed:
                raise RuntimeError(f'{log} failed:\n' + '\n'.join(text.splitlines()[-30:]))
            return text

        try:
            run(['vlib', 'library'], 'vlib.log')
            run(['vmap', '-c'], 'vmap_init.log')
            run(['vmap', 'verify', 'library'], 'vmap.log')
            configs = ['asic', 'fpga'] if args.config == 'both' else [args.config]
            for config in configs:
                defines = ['+define+CHIPDESIGN_FPGA_MEM'] if config == 'fpga' else []
                run(['vlog', '-sv', '-work', 'verify', *defines, '-f', filelist, *extras], f'{config}_compile.log')
                tests = [
                    ('chipdesign_soc_tb', 'PASS: integrated SoC test OK', []),
                    ('chipdesign_dma_tb', 'PASS: NPU DMA test OK', []),
                    ('tb_picorv32_soc', 'ALL PICORV32 SOC TESTS PASSED', []),
                    ('tb_requant_unit', 'ALL REQUANT UNIT TESTS PASSED', []),
                    ('tb_tinycnn8_capacity', 'ALL FLAT CAPACITY TESTS PASSED', []),
                    ('tb_axi_sram_pipeline', 'ALL AXI SRAM PIPELINE TESTS PASSED', []),
                ]
                if args.profile_only:
                    tests = []
                elif args.test:
                    names = {test[0] for test in tests}
                    if set(args.test) - names:
                        parser.error('unknown --test top')
                    tests = [test for test in tests if test[0] in args.test]
                for top, marker, generics in tests:
                    name = f'{config}_{top}'
                    text = run(['vsim', '-c', 'verify.' + top, *generics, '-do',
                                'onerror {quit -code 1}; run 0; vcd off; run -all; quit -f'], name + '.log', marker)
                    results.append(dict(test=name, passed=True))
                    print('\n'.join(line for line in text.splitlines() if 'PASS' in line), flush=True)
                for index, value in enumerate(args.workload):
                    workload = Path(value).resolve()
                    shutil.copyfile(workload / 'workload.hex', snapshot / 'workload.hex')
                    name = f'{config}_workload_{index}'
                    text = run(['vsim', '-c', 'verify.kws_workload_tb', '+firmware=workload.hex', '-do',
                         'onerror {quit -code 1}; run -all; quit -f'], name + '.log', 'KWS_PASS')
                    normalized = output / (name + '_results.log')
                    normalized.write_text('\n'.join(line[2:] if line.startswith('# ') else line
                                                   for line in text.splitlines()) + '\n', encoding='utf-8')
                    # Independently recompute every raw logit and CPU prediction.
                    check = subprocess.run([sys.executable, str(ROOT / 'software/tools/compare_kws_workload.py'),
                        '--workload', str(workload), '--log', str(normalized)],
                        cwd=ROOT, capture_output=True, text=True, errors='replace', timeout=180)
                    (output / (name + '_golden.log')).write_text(check.stdout + check.stderr)
                    if check.returncode:
                        raise RuntimeError(check.stdout + check.stderr)
                    results.append(dict(test=name, workload=str(workload), passed=True))
                    print(f'PASS {name}: firmware logits and predictions match Python golden', flush=True)
                if args.profile_workload:
                    import hashlib
                    workload = Path(args.profile_workload).resolve()
                    firmware = (workload / 'workload.hex').read_bytes()
                    (snapshot / 'workload.hex').write_bytes(firmware)
                    name = f'{config}_access_profile'
                    text = run(['vsim', '-c', 'verify.kws_access_profile_tb', '+firmware=workload.hex',
                         '-do', 'onerror {quit -code 1}; run -all; quit -f'], name + '.log', 'KWS_PASS')
                    normalized = output / (name + '_results.log')
                    normalized.write_text('\n'.join(line[2:] if line.startswith('# ') else line
                        for line in text.splitlines()) + '\nACCESS_IMAGE sha256=' +
                        hashlib.sha256(firmware).hexdigest() + '\n', encoding='utf-8')
                    sys.path.insert(0, str(ROOT / 'software/tools'))
                    from analyze_kws_access_profile import parse, transactions
                    events, samples = parse(normalized.read_text(encoding='utf-8'))
                    rows, incomplete = transactions(events)
                    results.append(dict(test=name, passed=True, transactions=len(rows), samples=len(samples)))
                    print(f'PASS {name}: {len(rows)} matched native/AXI transactions', flush=True)
        finally:
            (output / 'results.json').write_text(json.dumps(results, indent=2))
            print(f'Logs: {output}', flush=True)


if __name__ == '__main__':
    main()
