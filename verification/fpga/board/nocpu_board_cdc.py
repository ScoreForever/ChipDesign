"""Real-board stopped-TCK and reset checks. Requires the validated image loaded."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'software/host'))
from nocpu_jtag import Npu, Op, JtagTransport, TclRpc
from npu_program import compile_model, reference, encode_tensor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    report = {'passed': False, 'checks': [], 'physical_reset': 'pending'}
    rpc = TclRpc(timeout=20)
    tx = JtagTransport(rpc, timeout=20)
    npu = Npu(tx)
    original = rpc.execute
    def logged(command):
        result = original(command)
        with (out/'commands.jsonl').open('a') as f:
            f.write(json.dumps({'time': time.time(), 'command': command, 'result': result})+'\n')
        return result
    rpc.execute = logged
    try:
        npu.command(Op.RESET)
        nodes=[]; src='x'
        for i in range(16):
            weights=[0]*9; weights[4]=1
            nodes += [dict(op='Conv',input=src,output=f'a{i}',channels=1,
                           kernel=3,pads=[1]*4,weights=weights,bias=[0]),
                      dict(op='Requant',input=f'a{i}',output=f'q{i}',
                           multiplier=[0x4000],shift=[1])]
            src=f'q{i}'
        model=dict(input=dict(name='x',shape=[64,64,1]),nodes=nodes)
        frame=[i%16-8 for i in range(4096)]
        pkg=compile_model(model,cols=npu.read(0x58)&65535)
        pkg.save(out/'package')
        inp=encode_tensor(frame,'i4')
        expected=encode_tensor(reference(model,frame)[src],pkg.manifest['output_dtype'])
        (out/'input.bin').write_bytes(inp); (out/'expected.bin').write_bytes(expected)
        def prepare():
            npu.command(Op.RESET); npu.load_model(pkg); npu.write(0x28,1_000_000_000)
            npu.command(Op.INPUT_BEGIN)
            for i in range(0,len(inp),4):
                npu.write(0x20000+pkg.manifest['input_base']+i,int.from_bytes(inp[i:i+4],'little'))
            npu.command(Op.INPUT_COMMIT)
        def start_busy(action=None):
            # Execute scans inside one OpenOCD RPC, avoiding millisecond host
            # round trips between observing busy and issuing the test command.
            operations=[(Op.START,0),(Op.READ,4)]
            if action is not None: operations.append((action,0))
            commands=[]
            for i,(op,addr) in enumerate(operations):
                commands.append(f'drscan npu.tap 32 0 32 0x{addr:x} 8 0x{int(op):x} -endstate IDLE')
                commands.append('drscan npu.tap 32 0 32 0 8 0 -endstate IDLE')
                commands.append(f'set r{i} [drscan npu.tap 32 0 32 0 8 0 -endstate IDLE]')
            commands.append('join [list '+ ' '.join(f'$r{i}' for i in range(len(operations)))+'] |')
            values=[[int(v,16) for v in item.split()] for item in rpc.execute('; '.join(commands)).split('|')]
            if any(len(v)!=3 or not v[2]&128 or v[2]&64 for v in values):
                raise RuntimeError(f'burst mailbox did not complete: {values}')
            status=values[1][0]
            if not status&1: raise RuntimeError('job finished before busy could be confirmed')
            if action==Op.START and values[2][2]&31!=4: raise RuntimeError('duplicate START not rejected busy')
            if action==Op.RESET and values[2][2]&31: raise RuntimeError('RESET failed')
            return status
        # No JTAG RPC during sleep: idle TAP has no requested clock cycles.
        prepare(); status=start_busy(); before=time.monotonic()
        time.sleep(1)
        paused=time.monotonic()-before
        status_after=npu.read(4)
        if not status_after&8 or status_after&65: raise RuntimeError('job did not finish independently of TCK')
        actual=b''.join(npu.read(0x20000+pkg.manifest['output_base']+i).to_bytes(4,'little')
                        for i in range(0,len(expected),4))[:len(expected)]
        (out/'actual.bin').write_bytes(actual)
        if actual!=expected: raise RuntimeError('output differs after TCK pause')
        report['checks'].append(dict(name='pause_TCK_while_busy',before=status,after=status_after,
                                     pause_seconds=paused,output_matches=True))
        prepare(); status=start_busy(Op.START)
        report['checks'].append(dict(name='repeat_START_while_busy',before=status,expected_error=4))
        # Reload to give the reset a full workload window.
        prepare(); status=start_busy(Op.RESET)
        after=npu.status()
        if any(after[k] for k in ['busy','model_ready','input_ready','done','faulted','error']):
            raise RuntimeError('RESET did not cancel busy job')
        report['checks'].append(dict(name='protocol_RESET_while_busy',before=status,after=after))
        prepare(); npu.write(0x28,1000); npu.command(Op.START); time.sleep(.02)
        fault=npu.status()
        if not fault['faulted'] or fault['error']!=10: raise RuntimeError('watchdog did not fault')
        npu.command(Op.RESET)
        report['checks'].append(dict(name='watchdog_timeout_recovery',fault=fault,after=npu.status()))
        # Verify a complete golden result after cancellation and timeout recovery.
        npu.load_model(pkg); npu.write(0x28,1_000_000_000)
        if npu.infer(inp,timeout=20)!=expected: raise RuntimeError('post-reset inference mismatch')
        report['checks'].append(dict(name='fresh_inference_after_recovery',output_matches=True))
        npu.command(Op.RESET)
        report['final_status']=npu.status(); report['passed']=True
    finally:
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        rpc.close()
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
