#!/usr/bin/env python3
"""Convert an Xcelium workload VCD window to mapped, bit-level SAIF (stdlib only)."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def build_path(path):
    root = Path(__file__).resolve().parents[2] / 'build'
    path = Path(path).resolve()
    if root not in path.parents:
        raise ValueError('Output must be inside worktree build/')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def window(log):
    begin = re.findall(r'^SAIF_BEGIN time_ns=(\d+) cycle=(\d+)$', log, re.M)
    end = re.findall(r'^SAIF_END time_ns=(\d+) cycle=(\d+) cycles=(\d+)$', log, re.M)
    if len(begin) != 1 or len(end) != 1 or not re.search(r'^KWS_PASS count=\d+$', log, re.M):
        raise ValueError('Need exactly one complete activity window and KWS_PASS')
    a, b = int(begin[0][0]) * 1000, int(end[0][0]) * 1000
    cycles = int(end[0][2])
    if b <= a or cycles <= 0 or b-a != cycles*20000:
        raise ValueError('Window does not match the 50 MHz workload clock')
    return a, b, cycles


def atom(s):
    s = s.lstrip('\\')
    return re.sub(r'([\\()\[\]])', r'\\\1', s)


def convert(vcd, log, output, source='kws_saif_tb/workload/dut'):
    begin, end, cycles = window(log.read_text(encoding='utf-8-sig'))
    signals, scopes, scope_kinds, names = {}, [], [], []
    # One state per VCD identifier, retaining every hierarchical alias.
    with vcd.open() as stream:
        header = ''
        for line in stream:
            header += line
            if '$enddefinitions' in line:
                break
        scale = re.search(r'\$timescale\s*(\d+)\s*(s|ms|us|ns|ps|fs)\s*\$end', header)
        if not scale:
            raise ValueError('VCD timescale missing')
        tick_ps = int(scale[1]) * dict(s=10**12, ms=10**9, us=10**6, ns=1000, ps=1, fs=.001)[scale[2]]
        for command in re.findall(r'\$(.*?)\$end', header, re.S):
            fields = command.split()
            if not fields:
                continue
            if fields[0] == 'scope':
                scopes.append(fields[2])
                scope_kinds.append(fields[1])
            elif fields[0] == 'upscope':
                scopes.pop()
                scope_kinds.pop()
            elif fields[0] == 'var':
                _, kind, width, code, name, *indices = fields
                prefix = '/'.join(scopes)
                if prefix != source and not prefix.startswith(source+'/'):
                    continue
                if kind in ('real', 'realtime', 'string'):
                    continue
                width = int(width)
                if code not in signals:
                    signals[code] = dict(width=width, value='x'*width, last=begin,
                        dwell=[[0, 0, 0] for _ in range(width)], tc=[0]*width)
                elif signals[code]['width'] != width:
                    raise ValueError('Inconsistent VCD alias width')
                indices_text = ''.join(indices)
                match = re.fullmatch(r'\[(-?\d+):(-?\d+)\]', indices_text)
                if match:
                    left, right = map(int, match.groups())
                    bits = list(range(left, right + (1 if right >= left else -1), 1 if right >= left else -1))
                    if len(bits) != width:
                        raise ValueError('VCD range width mismatch')
                else:
                    bits = list(range(width-1, -1, -1))
                start = len(source.split('/'))
                relative = []
                generated = []
                # Vivado folds named generate scopes into dot-qualified cell/net
                # names, e.g. g_core.i_tinycnn8_npu and rows[0].cols[0].psum_q.
                for scope, scope_kind in zip(scopes[start:], scope_kinds[start:]):
                    if scope_kind in ('begin', 'fork', 'generate'):
                        generated.append(scope)
                    else:
                        relative.append('.'.join(generated+[scope]))
                        generated = []
                name = '.'.join(generated+[name])
                for pos, bit in enumerate(bits):
                    bit_name = name + (f'[{bit}]' if width > 1 else indices_text)
                    names.append((relative, bit_name, code, pos))
        if not names:
            raise ValueError('Source hierarchy has no VCD signals')
        now = 0
        def update(code, value, at):
            state = signals.get(code)
            if state is None or at >= end:
                return
            width = state['width']
            value = value.lower()
            value = value.rjust(width, value[0] if value[0] in 'xz' else '0')[-width:]
            if at < begin:
                state['value'] = value
                return
            dt = at-state['last']
            if dt < 0:
                raise ValueError('Non-monotonic VCD timestamps')
            for i, old in enumerate(state['value']):
                state['dwell'][i][0 if old == '0' else 1 if old == '1' else 2] += dt
                if at > begin and old in '01' and value[i] in '01' and old != value[i]:
                    state['tc'][i] += 1
            state['last'], state['value'] = at, value
        for line in stream:
            line = line.strip()
            if not line or line.startswith('$'):
                continue
            if line[0] == '#':
                now = round(int(line[1:])*tick_ps)
                if now >= end:
                    break
            elif line[0] in 'bB':
                value, code = line[1:].split()
                update(code, value, now)
            elif line[0] in '01xXzZ':
                update(line[1:], line[0], now)
        if now < end:
            raise ValueError('VCD ends before activity window')
    for state in signals.values():
        for i, value in enumerate(state['value']):
            state['dwell'][i][0 if value == '0' else 1 if value == '1' else 2] += end-state['last']
    tree = dict(children={}, nets={})
    for relative, name, code, pos in names:
        node = tree
        for scope in ['u_soc']+relative:
            node = node['children'].setdefault(scope, dict(children={}, nets={}))
        if name in node['nets']:
            raise ValueError('Duplicate hierarchical signal')
        node['nets'][name] = (signals[code]['dwell'][pos], signals[code]['tc'][pos])
    with output.open('w', newline='\n') as f:
        f.write('(SAIFILE\n (SAIFVERSION "2.0")\n (DIRECTION "backward")\n'
                ' (DESIGN "fpga_top")\n (DATE "workload capture")\n (VENDOR "ChipDesign")\n'
                ' (PROGRAM_NAME "kws_vcd_to_saif")\n (VERSION "1")\n'
                ' (DIVIDER /)\n (TIMESCALE 1 ps)\n'
                f' (DURATION {end-begin})\n')
        def emit(name, node, level):
            pad = ' '*level
            f.write(f'{pad}(INSTANCE {atom(name)}\n')
            if node['nets']:
                f.write(pad+' (NET\n')
                for net, (dwell, tc) in sorted(node['nets'].items()):
                    t0, t1, tx = dwell
                    f.write(f'{pad}  ({atom(net)} (T0 {t0}) (T1 {t1}) (TX {tx}) (TC {tc}) (IG 0))\n')
                f.write(pad+' )\n')
            for child, value in sorted(node['children'].items()):
                emit(child, value, level+1)
            f.write(pad+')\n')
        emit('fpga_top', tree, 1)
        f.write(')\n')
    vcd_hash = sha256_file(vcd)
    saif_hash = sha256_file(output)
    metadata = dict(schema=1, source_hierarchy=source, implementation_hierarchy='fpga_top/u_soc',
        generate_scope_mapping='begin/fork/generate scopes folded into dot-qualified cells or nets',
        window='first input write request through post-clear idle response; includes CPU scaling',
        begin_ps=begin, end_ps=end, duration_ps=end-begin, cycles=cycles, period_ns=20,
        bit_nets=len(names), unique_identifiers=len(signals),
        unknown_bit_time_ps=sum(sum(x[2] for x in st['dwell']) for st in signals.values()),
        transition_policy='TC counts 0/1 changes, including same-time glitches; X/Z dwell is TX',
        vcd_sha256=vcd_hash, saif_sha256=saif_hash)
    output.with_suffix('.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print(json.dumps(metadata))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--vcd', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--source-hierarchy', default='kws_saif_tb/workload/dut')
    args = parser.parse_args()
    convert(args.vcd, args.log, build_path(args.output), args.source_hierarchy)


if __name__ == '__main__':
    main()
