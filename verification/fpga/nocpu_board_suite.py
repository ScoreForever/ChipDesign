#!/usr/bin/env python3
"""ABI 2.1 board regression; runs against a manually programmed FPGA."""
from __future__ import annotations
import argparse
import hashlib
import json
import struct
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software/host'))
sys.path.insert(0, str(ROOT / 'hardware/npu/scripts'))
from nocpu_jtag import Npu, Op, TclRpc, JtagTransport
from npu_program import Package, compile_model, reference
from run_program_regression import fixtures, encoded


def sha(data):
    return hashlib.sha256(data).hexdigest()


def expect_error(fn, code):
    try:
        fn()
    except RuntimeError as exc:
        if str(exc).startswith(f'NPU error {code}:'):
            return str(exc)
        raise
    raise AssertionError(f'expected NPU error {code}, command succeeded')


def raw_upload(npu, pkg):
    m = pkg.manifest
    npu.command(Op.MODEL_BEGIN)
    for reg, key in zip(range(0x10, 0x28, 4), ('model_bytes', 'op_count', 'input_base', 'input_bytes', 'output_base', 'output_bytes')):
        npu.write(reg, m[key])
    npu.write(0x64, int(m['input_dtype'] == 'i32'))
    for off in range(0, len(pkg.image), 4):
        npu.write(0x10000 + off, int.from_bytes(pkg.image[off:off+4], 'little'))


def execute(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'ABI 2.1 no-CPU board functional suite', 'started_utc': datetime.now(timezone.utc).isoformat(),
              'passed': False, 'cases': [], 'manual_pending': ['repeat START while confirmed busy', 'watchdog timeout and recovery', 'RESET while confirmed busy', 'pause TCK while confirmed busy', 'physical reset button', 'power cycle/reprogram', 'electrical/timing review'],
              'limitations': ['Board bitstream identity and build provenance require human review', 'Golden is project software reference, not independent for all cases']}
    rpc = None
    try:
        bit = args.bitstream.read_bytes()
        if sha(bit).lower() != args.sha256.lower():
            raise ValueError('bitstream SHA-256 mismatch')
        report['bitstream'] = {'path': str(args.bitstream.resolve()), 'sha256': sha(bit), 'identity': 'human supplied; board configuration not read back'}
        provenance_bytes = args.provenance.read_bytes()
        provenance = json.loads(provenance_bytes)
        if provenance.get('synthesis_only') is not False or not provenance.get('source_sha256'):
            raise ValueError('provenance must describe an implemented build')
        mismatches = [name for name, expected in provenance['source_sha256'].items()
                      if not (ROOT / name).is_file() or sha((ROOT / name).read_bytes()) != expected]
        report['provenance'] = {'path': str(args.provenance.resolve()), 'sha256': sha(provenance_bytes), 'source_mismatches': mismatches}
        report['git_commit'] = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
        if mismatches:
            raise ValueError('source differs from build provenance: ' + ', '.join(mismatches))
        rpc = TclRpc(args.host, args.port, timeout=args.timeout)
        transport = JtagTransport(rpc, timeout=args.timeout)
        original_exchange = transport.exchange
        def logged_exchange(op, addr=0, data=0):
            record = {'op': int(op), 'address': addr, 'data': data}
            try:
                value = original_exchange(op, addr, data)
                record['result'] = value
                return value
            except Exception as exc:
                record['error'] = f'{type(exc).__name__}: {exc}'
                raise
            finally:
                with (out / 'commands.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps(record) + '\n')
        transport.exchange = logged_exchange
        npu = Npu(transport)
        status = npu.status()
        report['initial_status'] = status
        if status['busy'] or status['done'] or status['faulted']:
            raise RuntimeError('device must be idle with no pending result or fault; inspect and reset manually')
        caps = {f'{x:#x}': npu.read(x) for x in (0, 0x50, 0x54, 0x58, 0x5c, 0x60)}
        report['capabilities'] = caps
        cols = caps['0x58'] & 0xffff
        if caps['0x58'] >> 16 != 4 or cols not in (4, 8) or caps['0x50'] != 8192 or caps['0x54'] != 8192:
            raise RuntimeError('expected dual 8 KiB SRAM and 4x4 or 4x8 array')
        fixture_list = list(fixtures())
        for name, model, frame in fixture_list:
            for fuse in (True, False):
                case = f'{name}_{"fused" if fuse else "plain"}'
                try:
                    pkg = compile_model(model, fuse=fuse, cols=cols)
                except ValueError as exc:
                    expected_rejection = name == 'tinycnn' and not fuse and 'activation capacity exceeded' in str(exc)
                    report['cases'].append({'name': case, 'status': 'EXPECTED_HOST_REJECTION' if expected_rejection else 'SKIP', 'reason': str(exc)})
                    continue
                case_dir = out / case
                case_dir.mkdir()
                pkg.save(case_dir / 'package')
                inp = encoded(frame, model['input'].get('dtype', 'i8'))
                expected_values = reference(model, frame)[model['nodes'][-1]['output']]
                expected = encoded(expected_values, pkg.manifest['output_dtype'])
                (case_dir/'input.bin').write_bytes(inp)
                (case_dir/'expected.bin').write_bytes(expected)
                entry = {'name': case, 'status': 'FAIL', 'input_sha256': sha(inp), 'expected_sha256': sha(expected), 'runs': []}
                report['cases'].append(entry)
                npu.load_model(pkg)
                for i in range(args.repeat):
                    run_frame = frame if i % 2 == 0 else list(reversed(frame))
                    run_input = encoded(run_frame, model['input'].get('dtype', 'i8'))
                    run_expected = encoded(reference(model, run_frame)[model['nodes'][-1]['output']], pkg.manifest['output_dtype'])
                    (case_dir/f'input_{i+1:03d}.bin').write_bytes(run_input)
                    (case_dir/f'expected_{i+1:03d}.bin').write_bytes(run_expected)
                    actual = npu.infer(run_input, timeout=args.timeout)
                    (case_dir/f'actual_{i+1:03d}.bin').write_bytes(actual)
                    entry['runs'].append({'input_sha256': sha(run_input), 'expected_sha256': sha(run_expected), 'output_sha256': sha(actual), 'matches': actual == run_expected})
                    if actual != run_expected:
                        raise AssertionError(f'{case}: result mismatch at repeat {i+1}')
                entry['status'] = 'PASS'
                print('PASS', case, flush=True)
        name, model, frame = fixture_list[0]
        base = compile_model(model, cols=cols)
        errors = []
        report['negative_cases'] = errors
        def check(label, fn, code):
            message = expect_error(fn, code)
            errors.append({'name': label, 'expected_error': code, 'actual': message, 'status': 'PASS'})
            print('PASS', label, flush=True)
        npu.command(Op.RESET)
        check('start_without_model', lambda: npu.command(Op.START), 5)
        check('input_commit_without_begin', lambda: npu.command(Op.INPUT_COMMIT), 5)
        npu.load_model(base)
        check('start_without_input', lambda: npu.command(Op.START), 5)
        npu.command(Op.INPUT_BEGIN)
        check('incomplete_input_commit', lambda: npu.command(Op.INPUT_COMMIT), 7)
        check('input_out_of_order', lambda: npu.write(0x20000 + base.manifest['input_base'] + 4, 0), 6)
        npu.command(Op.RESET)
        npu.command(Op.MODEL_BEGIN)
        check('incomplete_model_commit', lambda: npu.command(Op.MODEL_COMMIT), 7)
        npu.command(Op.RESET)
        mutations = [('illegal_descriptor_opcode', 0, 99, 18), ('reserved_word', 44, 1, 18),
                     ('invalid_stride', 20, 0, 18), ('invalid_shape', 40, 0, 19),
                     ('invalid_destination', 8, 8188, 20), ('unknown_source', 4, 2048, 21)]
        for label, offset, value, error in mutations:
            broken = bytearray(base.image)
            struct.pack_into('<I', broken, offset, value)
            raw_upload(npu, Package(bytes(broken), base.manifest))
            check(label, lambda: npu.command(Op.MODEL_COMMIT), error)
            npu.command(Op.RESET)
        npu.load_model(base)
        inp = encoded(frame, model['input'].get('dtype', 'i8'))
        npu.command(Op.INPUT_BEGIN)
        for off in range(0, len(inp), 4):
            npu.write(0x20000 + base.manifest['input_base'] + off, int.from_bytes(inp[off:off+4].ljust(4, b'\0'), 'little'))
        npu.command(Op.INPUT_COMMIT)
        npu.command(Op.START)
        deadline = time.monotonic() + args.timeout
        while not (npu.read(4) & 8):
            if time.monotonic() > deadline:
                raise TimeoutError('completion timeout; inspect status before retrying')
        check('start_before_ack', lambda: npu.command(Op.START), 9)
        check('input_before_ack', lambda: npu.command(Op.INPUT_BEGIN), 9)
        npu.command(Op.ACK)
        expected = encoded(reference(model, frame)[model['nodes'][-1]['output']], base.manifest['output_dtype'])
        if npu.infer(inp, timeout=args.timeout) != expected:
            raise AssertionError('post-error recovery result differs from reference')
        report['negative_cases'] = errors
        report['final_status'] = npu.status()
        skipped = [c['name'] for c in report['cases'] if c['status'] == 'SKIP']
        report['skipped'] = skipped
        report['passed'] = not skipped
        if skipped:
            report['error'] = 'one or more requested cases skipped; review compile errors'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['traceback'] = traceback.format_exc()
    finally:
        if rpc is not None:
            rpc.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Evidence:', out, 'PASS' if report['passed'] else 'INCOMPLETE/FAIL', flush=True)
    if report.get('error'):
        print(report['error'], file=sys.stderr)
    return 0 if report['passed'] else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bitstream', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--provenance', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=6666)
    p.add_argument('--repeat', type=int, default=2)
    p.add_argument('--timeout', type=float, default=30)
    args = p.parse_args()
    if args.repeat < 1 or args.timeout <= 0:
        p.error('repeat and timeout must be positive')
    return execute(args)

if __name__ == '__main__':
    sys.exit(main())
