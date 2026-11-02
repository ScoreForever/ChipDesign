"""Run the real TAP regression and review exact bundled-data CDC endpoints.

The raw Vivado report is retained; this does not create CDC waivers. Static
results apply only to the supplied routed build, not a future implementation.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--static-evidence', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rtl = root / 'hardware/soc/rtl/control/npu_jtag_transport.sv'
    bench = Path(__file__).with_name('tb_npu_jtag_cdc.sv')
    image = out / 'test.vvp'
    for name, command in (
        ('compile', ['iverilog', '-g2012', '-s', 'tb_npu_jtag_cdc', '-o',
                     str(image), str(rtl), str(bench)]),
        ('sim', ['vvp', str(image)]),
    ):
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        log = result.stdout + result.stderr
        (out / f'{name}.log').write_text(log, encoding='utf-8')
        if result.returncode or (name == 'sim' and 'PASS NPU_JTAG_CDC:' not in log):
            raise RuntimeError(f'{name} failed: {log}')
    prefix = 'u_soc/i_host/'
    expected = set()
    for target, offset, width in [('cmd_data_o', 0, 32), ('cmd_addr_o', 32, 32),
                                  ('cmd_op_o', 64, 8)]:
        for bit in range(width):
            expected.add((f'{prefix}req_payload_reg[{offset+bit}]/C',
                          f'{prefix}{target}_reg[{bit}]/D'))
    for field, width in [('data', 32), ('error', 5)]:
        for bit in range(width):
            expected.add((f'{prefix}rsp_mailbox_{field}_reg[{bit}]/C',
                          f'{prefix}reply_{field}_reg[{bit}]/D'))
    report = args.static_evidence / 'route_cdc.rpt'
    text = report.read_text()
    rows = re.findall(r'^\s*\d+\s+(CDC-\d+)\s+(Warning|Critical|Error)\s+(.+)$',
                      text, re.M)
    actual = []
    for rule, severity, row in rows:
        if rule != 'CDC-15' or severity != 'Warning' or 'Max Delay Datapath Only' not in row:
            raise RuntimeError(f'Unreviewed CDC finding: {rule} {severity} {row}')
        actual.append(tuple(row.split()[-2:]))
    if len(actual) != 109 or set(actual) != expected:
        raise RuntimeError('CDC endpoints differ from reviewed 72 request / 37 response bits')
    with (args.static_evidence / 'route_npu_jtag_cdc.tsv').open() as f:
        audit = list(csv.DictReader(f, delimiter='\t'))
    for group, width in [('request_payload', 72), ('response_payload', 37)]:
        entry, = [r for r in audit if r['group'] == group]
        if (entry['kind'] != 'bundled_max_delay' or
            any(int(entry[k]) != width for k in ['source_registers', 'target_pins', 'paths']) or
            float(entry['bound_ns']) != 20 or float(entry['min_slack_ns']) < 0):
            raise RuntimeError(f'Invalid bundled-data physical constraints: {group}')
    for group in ['request_toggle', 'response_toggle']:
        for kind in ['second_stage_max', 'second_stage_min']:
            entry, = [r for r in audit if r['group'] == group and r['kind'] == kind]
            if int(entry['paths']) != 1 or float(entry['min_slack_ns']) < 0:
                raise RuntimeError(f'Invalid synchronizer timing: {group}/{kind}')
    inputs = [rtl, bench, report, args.static_evidence / 'route_npu_jtag_cdc.tsv']
    summary = dict(dynamic_simulation='PASS', raw_cdc15_warnings=109,
                   reviewed_request_bits=72, reviewed_response_bits=37,
                   unexpected_warning_rows=0, waivers_created=False,
                   limitation='Digital simulation does not model metastability; physical checks apply to supplied routed build only.',
                   sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    (out / 'review.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print('PASS: actual TAP dynamic regression; 109 exact bundled-data endpoints reviewed; raw warnings preserved')


if __name__ == '__main__':
    main()
