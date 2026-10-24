#!/usr/bin/env python3
"""Nominal FT232H frequency sweep; CPU stays halted and scratch SRAM is restored."""
import argparse
from datetime import datetime, timezone
import random
import workflow as w

def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--output',required=True);a=p.parse_args()
    out=w.bounded_output(a.output);m=w.json.loads((out/'manifest.json').read_text())
    dest=w.result_directory(out,'link')
    report=dict(status='RUNNING',manifest_sha256=w.digest(out/'manifest.json'),cases=[],
        started_utc=datetime.now(timezone.utc).isoformat(),scope='Nominal adapter settings; not measured frequency or electrical eye margin')
    rpc=None;backup=None
    try:
        w.validate_manifest(out,m);rpc=w.Rpc();rpc.call('halt');rpc.call('adapter speed 500')
        backup=rpc.read(0x80001f40,16)
        rng=random.Random(20261020)
        for khz in (10,100,500,1000):
            rpc.call(f'adapter speed {khz}')
            for trial in range(8):
                expected=[rng.getrandbits(32) for _ in range(16)]
                rpc.call('write_memory 0x80001f40 32 {'+' '.join(hex(x) for x in expected)+'}')
                got=rpc.read(0x80001f40,16);boot=rpc.read(0x10000,3)
                if got!=expected or boot!=[0x800002b7,0x00028293,0x00028067]:
                    raise AssertionError(f'Link mismatch at {khz}kHz trial{trial}')
                report['cases'].append(dict(khz=khz,trial=trial,expected=expected,readback=got,bootrom=boot))
                w.save(dest/'summary.json',report)
            print(f'PASS JTAG {khz}kHz: 8 trials x16 words + BootROM',flush=True)
        report['status']='PASS'
    except BaseException as e:
        report.update(status='FAIL',error=str(e));raise
    finally:
        if rpc:
            try:
                rpc.call('adapter speed 500');rpc.call('halt')
                if backup is not None:
                    rpc.call('write_memory 0x80001f40 32 {'+' '.join(hex(x) for x in backup)+'}')
                    if rpc.read(0x80001f40,16)!=backup: raise AssertionError('Scratch restore failed')
            except BaseException as e:
                report.update(status='FAIL',restore_error=str(e));raise
            finally:rpc.close();w.save(dest/'summary.json',report)
        else:w.save(dest/'summary.json',report)
if __name__=='__main__': main()
