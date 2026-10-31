#!/usr/bin/env python3
"""Record a repeatable ABI 3.0 INT4 board smoke test; programming is manual."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software' / 'host'))
from nocpu_jtag import ABI, JtagTransport, Npu, TclRpc
from npu_program import Package


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'scope': 'ABI 3.0 INT4 JTAG capability and repeated model inference smoke test',
              'programming': 'manual; bitstream identity on board is not verified by this script',
              'runs': []}
    rpc = None
    try:
        bit = args.bitstream.resolve(strict=True)
        expected_hash = args.bitstream_sha256.lower()
        if digest(bit) != expected_hash:
            raise ValueError('bitstream SHA-256 mismatch')
        report['bitstream'] = {'path': str(bit), 'sha256': expected_hash}
        provenance = json.loads(args.provenance.read_text(encoding='utf-8'))
        if provenance.get('synthesis_only') is not False or not provenance.get('source_sha256'):
            raise ValueError('provenance must describe an implemented build with source hashes')
        changed = [name for name, sha in provenance['source_sha256'].items()
                   if not (ROOT / name).is_file() or digest(ROOT / name) != sha]
        report['source_mismatches'] = changed
        if changed:
            raise ValueError('current source differs from build provenance: ' + ', '.join(changed))
        report['provenance'] = {'path': str(args.provenance.resolve()), 'sha256': digest(args.provenance)}
        report['git_commit'] = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                               text=True, capture_output=True, check=True).stdout.strip()
        pkg = Package.load(args.package)
        frame = args.input.read_bytes()
        expected = args.expected.read_bytes()
        if len(frame) != pkg.manifest['input_bytes'] or len(expected) != pkg.manifest['output_bytes']:
            raise ValueError('input or expected length does not match manifest')
        for name, payload in [('model.bin', pkg.image), ('input.bin', frame), ('expected.bin', expected)]:
            (out / name).write_bytes(payload)
        (out / 'manifest.json').write_text(json.dumps(pkg.manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        report['inputs_sha256'] = {name: digest(out / name) for name in ('model.bin', 'manifest.json', 'input.bin', 'expected.bin')}
        rpc = TclRpc(args.host, args.port, timeout=args.timeout)
        npu = Npu(JtagTransport(rpc, timeout=args.timeout))
        caps = {f'0x{addr:02x}': npu.read(addr) for addr in (0, 4, 8, 0x50, 0x54, 0x58, 0x5c, 0x60)}
        report['capabilities'] = caps
        if caps['0x00'] != ABI or caps['0x04'] & 0x7f:
            raise RuntimeError('NPU must be idle, clear and unconfigured before test; inspect status')
        npu.load_model(pkg)
        for index in range(args.repeat):
            actual = npu.infer(frame, timeout=args.timeout)
            path = out / f'output_{index + 1:03d}.bin'
            path.write_bytes(actual)
            ok = actual == expected
            report['runs'].append({'index': index + 1, 'output': path.name,
                                   'sha256': digest(path), 'matches_expected': ok})
            if not ok:
                raise AssertionError(f'run {index + 1}: output differs from expected')
        report['final_status'] = npu.status()
        if report['final_status']['busy'] or report['final_status']['done'] or report['final_status']['faulted']:
            raise RuntimeError('unexpected final status')
        report['passed'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['traceback'] = traceback.format_exc()
    finally:
        if rpc is not None:
            rpc.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bitstream', type=Path, required=True)
    p.add_argument('--bitstream-sha256', required=True)
    p.add_argument('--provenance', type=Path, required=True)
    p.add_argument('--package', type=Path, required=True)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--expected', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True, help='new evidence directory')
    p.add_argument('--repeat', type=int, default=3)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=6666)
    p.add_argument('--timeout', type=float, default=10)
    args = p.parse_args()
    if args.repeat < 1 or args.timeout <= 0:
        p.error('repeat and timeout must be positive')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
