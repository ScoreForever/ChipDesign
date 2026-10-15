"""Validate a SoC workload log against the frozen package and input artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from kws_workload_common import build_directory, load_inputs, load_package
import golden_model

HEADER = re.compile(r'^KWS_HEADER schema=(\d+) count=(\d+) load=(\d+) error=(\d+) stack=(\d+) results=([0-9a-fA-F]{8})(?: batch=(\d+) pipeline=([01]))?$')
SAMPLE = re.compile(r'^KWS_SAMPLE index=(\d+) raw=([0-9a-fA-F]{8}(?:,[0-9a-fA-F]{8}){3}) prediction=(\d+) input=(\d+) compute=(\d+) read=(\d+) perf=(\d+) total=(\d+)(?: steady=(\d+))?$')
PASS = re.compile(r'^KWS_PASS count=(\d+)$')


def parse_log(text):
    header, samples, passed = None, {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('KWS_HEADER'):
            match = HEADER.fullmatch(line)
            if header is not None or not match or samples or passed is not None:
                raise ValueError('invalid/duplicate/out-of-order workload header')
            header = [None if v is None else int(v, 16 if i == 5 else 10) for i, v in enumerate(match.groups())]
        elif line.startswith('KWS_SAMPLE'):
            match = SAMPLE.fullmatch(line)
            if not match or header is None or passed is not None:
                raise ValueError('malformed or out-of-order sample')
            index, raw, prediction, inp, compute, read, perf, total, steady = match.groups()
            index = int(index)
            if index in samples or index != len(samples):
                raise ValueError('duplicate or nonsequential sample index')
            signed = lambda v: v - 2**32 if v >= 2**31 else v
            samples[index] = dict(raw=[signed(int(v, 16)) for v in raw.split(',')],
                prediction=int(prediction), input=int(inp), compute=int(compute),
                read=int(read), perf=int(perf), total=int(total))
            if steady is not None:
                samples[index]['steady'] = int(steady)
        elif line.startswith('KWS_PASS'):
            match = PASS.fullmatch(line)
            if not match or passed is not None or header is None:
                raise ValueError('invalid/duplicate PASS marker')
            passed = int(match[1])
        elif re.search(r'(^|\W)(FAIL|TIMEOUT|Fatal:|Error:)', line) or re.search(r'\*(?:E|F|SE),', line):
            raise ValueError('simulation log contains failure diagnostics')
    if header is None or passed is None:
        raise ValueError('missing workload header/PASS marker')
    schema, count, load, error, stack, address, batch, pipeline = header
    if schema not in (1, 2) or error or not 0 < stack < 256 or count < 2:
        raise ValueError('invalid schema/error/stack/count in header')
    if count != passed or len(samples) != count:
        raise ValueError('incomplete sample set or PASS count mismatch')
    for sample in samples.values():
        if any(not 0 < sample[k] < 2**32 for k in ('input', 'compute', 'read', 'perf', 'total')):
            raise ValueError('zero/overflow timing field')
        if sample['compute'] < sample['perf'] or sample['total'] < sum(sample[k] for k in ('input', 'compute', 'read')):
            raise ValueError('inconsistent timing segments')
    if not 0 < load < 2**32:
        raise ValueError('invalid model-load timing')
    if schema == 1:
        if batch is not None or any('steady' in s for s in samples.values()):
            raise ValueError('schema 1 contains schema 2 fields')
    else:
        if batch is None or not 0 < batch < 2**32:
            raise ValueError('missing/invalid batch timing')
        for i, sample in samples.items():
            steady = sample.get('steady', -1)
            if (i == 0 and steady != 0) or (i > 0 and not 0 < steady < 2**32):
                raise ValueError('missing/invalid steady timing')
        if batch < max(s['total'] for s in samples.values()) or batch < (
                sum(s['steady'] for s in samples.values()) + samples[count - 1]['perf']):
            raise ValueError('inconsistent batch timing')
    result = dict(schema=schema, count=count, load=load, stack=stack, address=address,
                  samples=list(samples.values()))
    if schema == 2:
        result.update(batch=batch, pipeline=pipeline)
    return result


def compare(workload, log):
    workload = Path(workload)
    manifest = json.loads((workload / 'manifest.json').read_text())
    for name, digest in manifest['sha256'].items():
        if hashlib.sha256((workload / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'artifact hash mismatch: {name}')
    result = parse_log(Path(log).read_text(encoding='utf-8-sig'))
    if result['schema'] != manifest['schema']:
        raise ValueError('artifact/log schemas differ')
    if result['schema'] == 2 and result['pipeline'] != manifest['pipeline']:
        raise ValueError('artifact/log pipeline switches differ')
    inputs, pkg = load_inputs(workload / 'inputs.npy'), load_package(workload / 'package.npz')
    if result['count'] != manifest['count'] or len(inputs) != result['count']:
        raise ValueError('artifact/log sample counts differ')
    if result['address'] != int(manifest['results_address'], 16):
        raise ValueError('result address differs from linked image')
    for i, sample in enumerate(result['samples']):
        expected = golden_model.infer(pkg, inputs[i])
        prediction = golden_model.argmax_with_fc_compare(expected, pkg['fc_cmp_mult'], pkg['fc_cmp_shift'])
        if sample['raw'] != expected.tolist():
            raise ValueError(f'sample {i}: raw logit mismatch: got {sample["raw"]}, expected {expected.tolist()}')
        if sample['prediction'] != prediction:
            raise ValueError(f'sample {i}: class mismatch: got {sample["prediction"]}, expected {prediction}')
    return result


def report(result):
    print(f'PASS: {result["count"]} samples, all four raw logits bit-exact and classes equal')
    print(f'Stack high-water mark: {result["stack"]}/256 bytes')
    print('Cycles and microseconds at 50 MHz (1 cycle = 0.02 us); model load is once per batch.')
    print('| Segment | Min cycles | Mean cycles | Max cycles | Mean us |')
    print('|---|---:|---:|---:|---:|')
    fields = ['load', 'input', 'compute', 'perf', 'read', 'total']
    if result['schema'] == 2:
        fields += ['steady', 'batch']
    for field in fields:
        values = ([result[field]] if field in ('load', 'batch') else
                  [s[field] for s in result['samples'][1 if field == 'steady' else 0:]])
        mean = sum(values) / len(values)
        print(f'| {field} | {min(values)} | {mean:.2f} | {max(values)} | {mean / 50:.2f} |')
    extra = result['schema'] == 2
    print('| Sample | Input | Start/done CPU | NPU PERF_TOTAL | Read/argmax | End-to-end |' + (' Steady |' if extra else ''))
    print('|---|---:|---:|---:|---:|---:|' + ('---:|' if extra else ''))
    for i, sample in enumerate(result['samples']):
        print('| ' + ' | '.join(map(str, [i] + [sample[k] for k in (['input', 'compute', 'perf', 'read', 'total'] + (['steady'] if extra else []))])) + ' |')


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--workload', required=True)
    p.add_argument('--log', required=True)
    p.add_argument('--json-output', help='optional report path inside build/')
    args = p.parse_args()
    result = compare(args.workload, args.log)
    report(result)
    if args.json_output:
        dest = Path(args.json_output).resolve()
        build_directory(dest.parent)
        dest.write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
