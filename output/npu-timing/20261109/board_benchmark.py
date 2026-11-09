"""Replay the seven RTL benchmark frames on the accepted FPGA image."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--checkout', type=Path, required=True)
p.add_argument('--build', type=Path, required=True)
p.add_argument('--rtl-summary', type=Path, required=True)
p.add_argument('--sha256', required=True)
p.add_argument('--output', type=Path, required=True)
a=p.parse_args()
root=a.checkout.resolve()
sys.path.insert(0,str(root/'software/host'))
from nocpu_jtag import Npu, Op, JtagTransport, TclRpc
from npu_program import compile_model, encode_tensor

out=a.output.resolve()
out.mkdir(parents=True,exist_ok=False)
report=dict(passed=False,started_utc=datetime.now(timezone.utc).isoformat(),models=[])
rpc=None
try:
    bit=a.build/'fpga_top.bit'
    digest=hashlib.sha256(bit.read_bytes()).hexdigest()
    if digest!=a.sha256:
        raise ValueError('bitstream SHA-256 differs from programming record')
    if 'Result: PASS' not in (a.build/'summary.md').read_text():
        raise ValueError('build acceptance failed')
    provenance=json.loads((a.build/'provenance.json').read_text())
    if provenance.get('synthesis_only') is not False:
        raise ValueError('implemented build required')
    for name,expected in provenance['source_sha256'].items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('source differs from build: '+name)
    rtl=json.loads(a.rtl_summary.read_text())
    for name,expected in rtl['sources_sha256'].items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('source differs from RTL regression: '+name)
    cases=[r for r in rtl['tests'] if r['case'].startswith('packaged_') and r['case'].endswith('_c8_f1_j0')]
    if len(cases)!=7:
        raise ValueError('expected all seven 8-column model cases')
    report['bitstream_sha256']=digest
    rpc=TclRpc(timeout=30)
    npu=Npu(JtagTransport(rpc,timeout=30))
    npu.command(Op.RESET)
    if npu.read(0)!=0x50000 or npu.read(0x58)!=0x80008:
        raise ValueError('ABI 5.0 / 8x8 required')
    for row in cases:
        name=row['case'][len('packaged_'):-len('_c8_f1_j0')]
        source=root/'software/zoo/pretrained'/name
        model=json.loads((source/'model.json').read_text())
        golden=json.loads((source/'golden.json').read_text())['frames'][0]
        pkg=compile_model(model,cols=8)
        inp=encode_tensor(golden['input'],pkg.manifest['input_dtype'])
        expected=bytes.fromhex(golden['output_hex'])
        entry=dict(model=name,runs=[])
        report['models'].append(entry)
        folder=out/name
        folder.mkdir()
        pkg.save(folder/'package')
        (folder/'input.bin').write_bytes(inp)
        (folder/'expected.bin').write_bytes(expected)
        npu.command(Op.RESET)
        npu.load_model(pkg)
        for repeat in range(2):
            actual=npu.infer(inp,timeout=30)
            perf=npu.performance()
            (folder/f'actual_{repeat}.bin').write_bytes(actual)
            matches=actual==expected
            cycles_match=perf['active_cycles']==row['cycles'][repeat]
            entry['runs'].append(dict(matches_expected=matches,cycles_match_rtl=cycles_match,
                                      rtl_cycles=row['cycles'][repeat],performance=perf))
            if not matches or not cycles_match:
                raise RuntimeError(f'{name} repeat {repeat}: output={matches}, cycles={cycles_match}')
        print('PASS',name,[r['performance']['active_cycles'] for r in entry['runs']],flush=True)
    npu.command(Op.RESET)
    report['final_status']=npu.status()
    report['passed']=True
except Exception as exc:
    report['error']=f'{type(exc).__name__}: {exc}'
    raise
finally:
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if rpc is not None:
        rpc.close()
