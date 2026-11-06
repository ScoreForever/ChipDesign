"""Run the published zoo packages/golden bytes on the 50 MHz production FPGA."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'software/host'))
from nocpu_jtag import Npu, Op, TclRpc, JtagTransport
from npu_program import Package, decode_tensor


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--build',type=Path,required=True)
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    bit=args.build/'fpga_top.bit'
    provenance=json.loads((args.build/'provenance.json').read_text())
    for name,digest in provenance['source_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    bit_hash=hashlib.sha256(bit.read_bytes()).hexdigest()
    if bit_hash!='8bdb83a721a06f6ce53aa84ce455f6c0da3353af582f7c56c4a18a248fdf2c2c':
        raise ValueError('expected the accepted production 50 MHz image')
    models=ROOT/'software/zoo/pretrained'
    manifest=json.loads((models/'MANIFEST.json').read_text())
    report=dict(passed=False,started_utc=datetime.now(timezone.utc).isoformat(),models=[],
                bitstream_sha256=bit_hash,source_mismatches=[],
                model_commit=subprocess.check_output(['git','rev-parse','origin/feature/int4-v2'],cwd=ROOT,text=True).strip(),
                scope='7 published models, all 16 golden frames, cold/warm repetition; no full dataset accuracy claim')
    rpc=TclRpc(timeout=30);original=rpc.execute
    def logged(command):
        result=original(command)
        with (out/'commands.jsonl').open('a') as f:
            f.write(json.dumps(dict(time=time.time(),command=command,result=result))+'\n')
        return result
    rpc.execute=logged
    try:
        npu=Npu(JtagTransport(rpc,timeout=30));npu.command(Op.RESET)
        report['capabilities']={f'{a:#x}':npu.read(a) for a in (0,0x50,0x54,0x58,0x6c,0x184,0x1a8)}
        assert report['capabilities']['0x58']==0x40008
        assert report['capabilities']['0x184']==report['capabilities']['0x1a8']==0
        for name in manifest:
            run=models/name;pkg=Package.load(run/'c8')
            golden=json.loads((run/'golden.json').read_text())
            entry=dict(model=name,model_bytes=len(pkg.image),op_count=pkg.manifest['op_count'],
                       activation_peak=pkg.manifest['activation_peak'],frames=[])
            report['models'].append(entry)
            for index,row in enumerate(golden['frames']):
                folder=out/name/f'frame{index:02d}';folder.mkdir(parents=True)
                inp=bytes.fromhex(row['input_hex']);expected=bytes.fromhex(row['output_hex'])
                (folder/'input.bin').write_bytes(inp);(folder/'expected.bin').write_bytes(expected)
                frame=dict(index=index,dataset_index=row['index'],label=row['label'],
                           reference_predicted=row['predicted'],runs=[])
                entry['frames'].append(frame)
                # Reload each frame so its cold/warm pair matches R.simulate.
                npu.command(Op.RESET);npu.load_model(pkg)
                for repeat in range(2):
                    actual=npu.infer(inp,timeout=30)
                    (folder/f'actual_{repeat}.bin').write_bytes(actual)
                    perf=npu.performance()
                    values=decode_tensor(actual,pkg.manifest['output_dtype'],pkg.manifest['output_elements'])
                    predicted=max(range(len(values)),key=values.__getitem__)
                    frame['runs'].append(dict(matches=actual==expected,predicted=predicted,
                                              label_match=predicted==row['label'],performance=perf))
                    if actual!=expected or predicted!=row['predicted']:
                        raise AssertionError(f'{name} frame {index} repeat {repeat}: output differs')
                print('PASS',name,index,[x['performance']['active_cycles'] for x in frame['runs']],flush=True)
            assert len(entry['frames'])==16
        npu.command(Op.RESET);report['final_status']=npu.status();report['passed']=True
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['finished_utc']=datetime.now(timezone.utc).isoformat()
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        rpc.close()
    print('ALL 7 MODELS / 112 GOLDEN FRAMES / 224 BOARD RUNS PASSED',flush=True)


if __name__=='__main__':main()
