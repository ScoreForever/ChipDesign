"""Test log parsing/comparison with a constructed log, never measured timings."""
import argparse
import json
import shutil
from compare_kws_workload import compare, parse_log
from kws_workload_common import build_directory, load_inputs, load_package
import golden_model


def check_workload(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    pkg = load_package(directory / 'package.npz')
    inputs = load_inputs(directory / 'inputs.npy')
    schema = manifest['schema']
    lines = [f'KWS_HEADER schema={schema} count={len(inputs)} load=1000 error=0 stack=128 '
             f'results={int(manifest["results_address"], 16):08x}']
    if schema == 2:
        lines[0] += f' batch={len(inputs) * 3700} pipeline={manifest["pipeline"]}'
    for i, frame in enumerate(inputs):
        raw = golden_model.infer(pkg, frame)
        prediction = golden_model.argmax_with_fc_compare(raw, pkg['fc_cmp_mult'], pkg['fc_cmp_shift'])
        lines.append(f'KWS_SAMPLE index={i} raw=' + ','.join(f'{int(v) & 0xffffffff:08x}' for v in raw)
            + f' prediction={prediction} input=200 compute=3100 read=300 perf=2805 total=3700')
        if schema == 2:
            lines[-1] += f' steady={3700 if i else 0}'
    lines.append(f'KWS_PASS count={len(inputs)}')
    fixture = directory / 'constructed_results.log'
    fixture.write_text('\n'.join(lines) + '\n')
    compare(directory, fixture)
    checks = 1

    def reject(text, full=False):
        nonlocal checks
        try:
            if full:
                path = directory / 'invalid_results.log'
                path.write_text(text)
                compare(directory, path)
            else:
                parse_log(text)
        except ValueError:
            checks += 1
            return
        raise AssertionError('invalid log was accepted')

    for i in range(len(inputs)):
        for c in range(4):
            bad = lines.copy()
            prefix, remainder = bad[i+1].split(' raw=')
            raw, fields = remainder.split(' ', 1)
            values = raw.split(',')
            values[c] = f'{int(values[c], 16) ^ 1:08x}'
            bad[i+1] = prefix + ' raw=' + ','.join(values) + ' ' + fields
            reject('\n'.join(bad), full=True)
        bad = lines.copy()
        bad[i+1] = bad[i+1].replace('prediction=', 'prediction=9')
        reject('\n'.join(bad), full=True)
    reject('\n'.join(lines[:-1]))
    reject('\n'.join(lines[1:]))
    reject('\n'.join(lines[:2] + [lines[1]] + lines[2:]))
    reject('\n'.join(lines + [lines[-1]]))
    reject('\n'.join(lines).replace('count=', 'count=9', 1))
    reject('\n'.join(lines).replace('error=0', 'error=1'))
    reject('\n'.join(lines).replace('stack=128', 'stack=256'))
    reject('\n'.join(lines).replace('perf=2805', 'perf=0'))
    reject('\n'.join(lines).replace('compute=3100', 'compute=1'))
    reject('\n'.join(lines).replace('total=3700', 'total=1'))
    reject('\n'.join(lines).replace('raw=', 'raw=x', 1))
    reject('\n'.join(lines) + '\nFAIL: simulator failed')
    reject('\n'.join(lines) + '\nxmsim: *E,ASSERT: simulator failed')
    reject('\n'.join(lines).replace('results=', 'results=f', 1), full=True)
    if schema == 2:
        reject('\n'.join(lines).replace('schema=2', 'schema=1'))
        reject('\n'.join(lines).replace(' batch=', ' batch=x', 1))
        reject('\n'.join(lines).replace(f'batch={len(inputs) * 3700}', 'batch=0'))
        reject('\n'.join(lines).replace(f'batch={len(inputs) * 3700}', 'batch=3700'))
        reject('\n'.join(lines).replace('steady=0', 'steady=1', 1))
        reject('\n'.join(lines).replace('steady=3700', 'steady=0', 1))
        reject('\n'.join(lines).replace('steady=3700', 'steady=4294967296', 1))
        reject('\n'.join(lines).replace(' steady=0', '', 1))
        reject('\n'.join(lines).replace('pipeline=' + str(manifest['pipeline']),
                                          'pipeline=' + str(1 - manifest['pipeline'])), full=True)
    else:
        reject('\n'.join(lines).replace('schema=1', 'schema=2'))
        reject('\n'.join(lines).replace('total=3700', 'total=3700 steady=0', 1))
    (directory / 'invalid_results.log').unlink()
    print(f'PASS schema {schema}: {checks} constructed-log checks (including every sample/lane 1-LSB error)')
    print(f'Fixture only, timings are invented for parser tests: {fixture}')


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--workload', required=True)
    args = p.parse_args()
    directory = build_directory(args.workload)
    check_workload(directory)
    # Exercise the complete legacy comparison path with unchanged artifacts
    # and a schema 1 manifest, in an explicitly constructed fixture directory.
    legacy = build_directory(directory / 'schema1_fixture')
    manifest = json.loads((directory / 'manifest.json').read_text())
    manifest['schema'] = 1
    for name in manifest['sha256']:
        shutil.copyfile(directory / name, legacy / name)
    (legacy / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    check_workload(legacy)


if __name__ == '__main__':
    main()
