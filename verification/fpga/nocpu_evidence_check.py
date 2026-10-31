#!/usr/bin/env python3
"""Audit a no-CPU FPGA regression evidence directory without changing it."""
import argparse
import hashlib
import json
from pathlib import Path

REQUIRED_FIXTURES = ('cnn', 'pool_first', 'mlp', 'i32_pool', 'strided', 'edges', 'long_k',
                     'tinycnn', 'host_i32_quant', 'host_i32_pool', 'tiny_padded', 'one_mac',
                     'negative_pool', 'full_activation', 'full_activation_8k', 'upper_activation', 'slot_pressure')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    try:
        report = json.loads((args.evidence/'report.json').read_text(encoding='utf-8'))
        cases = {entry['name']: entry for entry in report.get('cases', [])}
        required = {f'{name}_{mode}' for name in REQUIRED_FIXTURES for mode in ('fused', 'plain')}
        missing = sorted(required - cases.keys())
        if missing or not report.get('passed'):
            print('INCOMPLETE: missing cases or suite failed: ' + ', '.join(missing))
            return 1
        if len(cases) != len(report.get('cases', [])) or set(cases) != required:
            print('INCOMPLETE: duplicate or unexpected cases')
            return 1
        for name, case in cases.items():
            if name == 'tinycnn_plain' and case.get('status') == 'EXPECTED_HOST_REJECTION':
                continue
            if case.get('status') != 'PASS' or not case.get('runs'):
                print(f'INCOMPLETE: invalid case {name}')
                return 1
            for index, run in enumerate(case['runs'], 1):
                folder = args.evidence / name
                actual = (folder/f'actual_{index:03d}.bin').read_bytes()
                expected = (folder/f'expected_{index:03d}.bin').read_bytes()
                if actual != expected or hashlib.sha256(actual).hexdigest() != run.get('output_sha256') or run.get('matches') is not True:
                    print(f'MISMATCH: {name} run {index}')
                    return 1
                for prefix, data in [('expected', expected), ('input', (folder/f'input_{index:03d}.bin').read_bytes())]:
                    if hashlib.sha256(data).hexdigest() != run.get(prefix+'_sha256'):
                        print(f'MISMATCH: {name} run {index} {prefix}')
                        return 1
        required_negative = {'start_without_model':5, 'input_commit_without_begin':5, 'start_without_input':5,
                             'incomplete_input_commit':7, 'input_out_of_order':6, 'incomplete_model_commit':7,
                             'illegal_descriptor_opcode':18, 'reserved_word':18, 'invalid_stride':18,
                             'invalid_shape':19, 'invalid_destination':20, 'unknown_source':21,
                             'start_before_ack':9, 'input_before_ack':9}
        entries = report.get('negative_cases', [])
        negatives = {entry['name']:entry for entry in entries}
        if len(negatives) != len(entries) or set(negatives) != set(required_negative) or any(
            negatives[name].get('status') != 'PASS' or negatives[name].get('expected_error') != code or
            not negatives[name].get('actual', '').startswith(f'NPU error {code}:')
            for name, code in required_negative.items() if name in negatives
        ):
            print('INCOMPLETE: negative cases missing or invalid')
            return 1
        if report.get('manual_pending'):
            print('AUTOMATED EVIDENCE VERIFIED; MANUAL CHECKS INCOMPLETE: ' + ', '.join(report['manual_pending']))
            return 2
        print('AUTOMATED EVIDENCE VERIFIED; MANUAL review of physical tests and board identity is required')
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'INCOMPLETE: {exc}')
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
