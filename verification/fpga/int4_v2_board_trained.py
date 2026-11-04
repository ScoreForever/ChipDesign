"""ABI 4 trained-weight PTQ smoke test; tensor correctness, not dataset accuracy."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software/host'))
from nocpu_jtag import Npu, Op, TclRpc, JtagTransport, PERF_NAMES
from npu_program import from_tinycnn_npz, compile_model, reference, encode_tensor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--rtl-evidence', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    weights = ROOT / 'software/models/tinycnn8_flatten_strong.npz'
    model = from_tinycnn_npz(weights)
    rng = random.Random(20261023)
    frame = [rng.randrange(-8, 8) for _ in range(320)]
    rtl = json.loads((args.rtl_evidence / 'summary.json').read_text())
    case, = [x for x in rtl['tests'] if x['case'] == 'trained_c8_f1_j0']
    report = dict(passed=False, model_sha256=hashlib.sha256(weights.read_bytes()).hexdigest(),
                  limitation='INT8 export converted through lossy /16 PTQ; no dataset accuracy claim.', runs=[])
    rpc = TclRpc(timeout=30)
    original = rpc.execute
    def logged(command):
        result = original(command)
        with (args.output / 'commands.jsonl').open('a') as f:
            f.write(json.dumps(dict(time=time.time(), command=command, result=result)) + '\n')
        return result
    rpc.execute = logged
    try:
        npu = Npu(JtagTransport(rpc, timeout=30))
        npu.command(Op.RESET)
        pkg = compile_model(model, cols=npu.read(0x58) & 65535)
        pkg.save(args.output / 'package')
        trace = dict(depth=npu.read(0x184), max_ops=npu.read(0x1a8))
        if trace != dict(depth=0, max_ops=0):
            raise AssertionError(f'production trace configuration differs: {trace}')
        report['trace_geometry'] = trace
        npu.load_model(pkg)
        for index, values in enumerate((frame, list(reversed(frame)))):
            inp = encode_tensor(values, pkg.manifest['input_dtype'])
            expected = encode_tensor(reference(model, values)[model['nodes'][-1]['output']],
                                     pkg.manifest['output_dtype'])
            actual = npu.infer(inp, timeout=30)
            for name, data in [('input', inp), ('expected', expected), ('actual', actual)]:
                (args.output / f'{name}_{index}.bin').write_bytes(data)
            perf = npu.performance()
            matches = actual == expected
            # Cache residency can alter cycles; work counts must agree exactly.
            rtl_perf = dict(zip(PERF_NAMES, case['performance'][index]))
            work = {key: perf[key] == rtl_perf[key]
                    for key in ('writes', 'mac_issues')}
            report['runs'].append(dict(matches=matches, performance=perf, rtl_work_matches=work,
                                       rtl_cycles=case['cycles'][index],
                                       cycle_delta=perf['active_cycles']-case['cycles'][index]))
            if not matches or not all(work.values()):
                raise AssertionError('trained output or work counts differ from reference/RTL')
        npu.command(Op.RESET)
        report['final_status'] = npu.status()
        report['passed'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (args.output / 'report.json').write_text(json.dumps(report, indent=2)+'\n')
        rpc.close()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
