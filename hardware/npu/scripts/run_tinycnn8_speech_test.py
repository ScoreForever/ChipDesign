#!/usr/bin/env python3
"""Run identical speech vectors on fused/layered RTL and check every fused edge.

Requires only Python's standard library plus iverilog/vvp after vector export.
Real-speech provenance and every hex hash must be supplied by the exporter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
NPU = ROOT/'hardware/npu'
sys.path.insert(0, str(NPU/'tools'))
from check_fused_events import check_trace, lanes, read_hex, signed  # noqa: E402
from test_fused_events import negative_checks  # noqa: E402
from tinycnn8_golden import multiply_by_quantized_multiplier  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(argv, log, timeout=1800):
    with log.open('w') as stream:
        stream.write('$ '+' '.join(map(str, argv))+'\n'); stream.flush()
        result = subprocess.run(list(map(str, argv)), cwd=ROOT, text=True,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    text = log.read_text()
    if result.returncode or re.search(r'\b(?:FATAL|ERROR):', text):
        raise RuntimeError(f'{log}: {text[-2500:]}')
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vector-dir', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--allow-synthetic', action='store_true', help='explicit infrastructure smoke, never real-speech evidence')
    args = p.parse_args()
    d, output = args.vector_dir.resolve(), args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('--output must be a new or empty directory')
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((d/'manifest.json').read_text())
    kind = manifest.get('kind')
    if kind != 'real_speech' and not (args.allow_synthetic and kind == 'synthetic'):
        raise ValueError('requires real-speech provenance; synthetic must be explicitly labeled')
    if kind == 'real_speech' and manifest.get('split') != 'test':
        raise ValueError('speech RTL evaluation requires the held-out test split')
    count = read_hex(d/'count.hex')[0]
    if not 1 <= count <= 100 or manifest['count'] != count:
        raise ValueError('invalid vector count')
    if kind == 'real_speech' and len(manifest.get('samples', [])) != count:
        raise ValueError('missing per-sample provenance')
    if manifest.get('labels') != ['yes', 'no', 'up', 'down'] or manifest.get('model_head') != 'flatten':
        raise ValueError('incompatible model ABI')
    required = ['count', 'inputs', 'weights', 'logits', 'labels', 'pool1', 'pool2',
                'conv1_bias', 'conv1_mult', 'conv1_shift', 'conv2_bias', 'conv2_mult',
                'conv2_shift', 'fc_bias', 'fc_cmp_mult', 'fc_cmp_shift']
    for name in required:
        filename = name+'.hex'
        if sha(d/filename) != manifest['files_sha256'][filename]:
            raise ValueError(f'input hash mismatch: {filename}')
    for tool in ('iverilog', 'vvp'):
        if not shutil.which(tool): raise RuntimeError(f'missing {tool}')
    weights = read_hex(d/'weights.hex')
    if len(weights) != 256: raise ValueError('weight capacity')
    inputs_flat = read_hex(d/'inputs.hex')
    if len(inputs_flat) != count*320: raise ValueError('input count')
    inputs = [inputs_flat[i*320:(i+1)*320] for i in range(count)]
    bias = read_hex(d/'conv1_bias.hex')+read_hex(d/'conv2_bias.hex')+[0]*8+read_hex(d/'fc_bias.hex')
    mult = read_hex(d/'conv1_mult.hex')+read_hex(d/'conv2_mult.hex')+[0]*16
    shift = read_hex(d/'conv1_shift.hex')+read_hex(d/'conv2_shift.hex')+[0]*16
    for multiplier, sh in zip(mult[:16], shift[:16]):
        if signed(multiplier, 32) < 0 or not -31 <= signed(sh, 8) <= 31:
            raise ValueError('unsupported fused quantization parameters')
    sources = [NPU/'rtl'/f'{name}.sv' for name in ('ws_pe', 'ws_systolic_array', 'matrix_unit',
               'vector_unit', 'requant_unit', 'conv_window_addr_gen', 'conv2d_engine',
               'maxpool2x2_engine', 'tinycnn8_npu_top', 'fused_ws_array', 'requant_lane', 'npu_weight_store', 'tinycnn8_npu_fused')]
    tb = NPU/'tb/tb_tinycnn8_trained_model.sv'
    results = {}
    for name in ('fused', 'layered'):
        image = output/(name+'.vvp')
        argv = ['iverilog', '-g2012', '-s', 'tb_tinycnn8_trained_model', '-o', image]
        if name == 'fused': argv += ['-DTINYCNN8_CORE=tinycnn8_npu_fused', '-DTINYCNN8_FUSED_EVENTS']
        command(argv+sources+[tb], output/(name+'-compile.log'))
        argv = ['vvp', image, '+VECTOR_DIR='+str(d)]
        if name == 'fused': argv += ['+EVENT_TRACE='+str(output/'events.csv')]
        text = command(argv, output/(name+'.log'))
        if f'ALL TRAINED FLATTEN RTL VECTORS PASSED samples={count}' not in text:
            raise RuntimeError('missing explicit RTL PASS')
        rows = re.findall(r'SPEECH_RESULT sample=(\d+) logits=([0-9a-fA-F]+)', text)
        if [int(i) for i, _ in rows] != list(range(count)): raise ValueError('missing/duplicate speech results')
        results[name] = [lanes(int(word, 16), 32, 8) for _, word in rows]
    if results['fused'] != results['layered']: raise AssertionError('fused/layered raw logits differ')
    expected = read_hex(d/'logits.hex')
    if len(expected) != count*4: raise ValueError('golden logit count')
    for i, actual in enumerate(results['fused']):
        if actual[:4] != [signed(x, 32) for x in expected[i*4:(i+1)*4]] or any(actual[4:]):
            raise AssertionError(f'golden mismatch at sample {i}')
    events = check_trace(output/'events.csv', inputs, weights, bias, mult, shift, 4)
    if len(events) != count: raise AssertionError('missing event jobs')
    faults = negative_checks(output/'events.csv', inputs, weights, bias, mult, shift, 4)
    labels = read_hex(d/'labels.hex')
    cm, cs = read_hex(d/'fc_cmp_mult.hex'), read_hex(d/'fc_cmp_shift.hex')
    if len(labels) != count or any(x not in range(4) for x in labels) or len(cm) != 4 or len(cs) != 4:
        raise ValueError('classification metadata size/labels')
    confusion = [[0]*4 for _ in range(4)]
    for truth, raw in zip(labels, results['fused']):
        scores = [multiply_by_quantized_multiplier(v, signed(m, 32), signed(s, 8))
                  for v, m, s in zip(raw[:4], cm, cs)]
        guess = max(range(4), key=lambda i: scores[i])
        confusion[truth][guess] += 1
    summary = {'status': 'PASS', 'kind': kind, 'samples': count, 'logit_comparisons': count*4,
               'pooled_byte_comparisons': count*800, 'event_edges': sum(x['cycles']+1 for x in events),
               'jobs': events, 'negative_checks': faults, 'confusion': confusion,
               'sample_accuracy': sum(confusion[i][i] for i in range(4))/count,
               'vector_manifest_sha256': sha(d/'manifest.json'), 'package_sha256': manifest.get('package_sha256'),
               'source_sha256': {str(x.relative_to(ROOT)): sha(x) for x in
                    sources+[tb, NPU/'tb/fused_event_trace.svh', NPU/'tools/check_fused_events.py',
                             NPU/'tools/test_fused_events.py', NPU/'tools/tinycnn8_golden.py', Path(__file__).resolve()]},
               'iverilog': subprocess.run(['iverilog', '-V'], capture_output=True, text=True).stdout.splitlines()[0]}
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(f"PASS {kind}: {count} samples, {count*4} logits, {count*800} pooled bytes, "
          f"{summary['event_edges']} sampled edges, {len(faults)} negative checks")


if __name__ == '__main__': main()
