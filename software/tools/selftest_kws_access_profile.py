"""Constructed probe-log tests. These cycle numbers are never measurements."""
import argparse
import hashlib
from pathlib import Path
from analyze_kws_access_profile import ROOT, analyze


def fixture(schema=1):
    lines = [f'ACCESS_META schema={schema} period_ns=20 sample=posedge_pre_nba',
             'ACCESS_IMAGE sha256='+hashlib.sha256(b'00000000\n').hexdigest()]
    counters = {}
    def emit(kind, **fields):
        lines.append(kind+' '+' '.join(f'{k}={v:08x}' if k in {'addr','data','pc','insn'}
                                      else f'{k}={v}' for k,v in fields.items()))
    def sequence(key):
        seq = counters.get(key, 0)
        counters[key] = seq+1
        return seq
    def axi(port, ch, v, h, addr=0, data=0, id=0):
        seq = sequence((port,ch))
        for event, cycle in [('V',v),('H',h)]:
            emit('ACCESS_AXI', cycle=cycle, port=port, channel=ch, seq=seq,
                 event=event, addr=addr, data=data, id=id, resp=0, len=0,last=1)
    def transaction(port, addr, we, cycle):
        seq = sequence(port)
        source, sid = (0,0) if schema == 2 else ((0,1) if port=='I' else (1,2))
        target = 3 if addr < 0x80000000 else 1
        mid = sid | (source<<2)
        s, m = f'S{source}', f'M{target}'
        emit('ACCESS_OBI',cycle=cycle,port=port,seq=seq,event='REQ',we=we,addr=addr,data=0)
        emit('ACCESS_OBI',cycle=cycle+1,port=port,seq=seq,event='GNT',we=we,addr=addr,data=0)
        emit('ACCESS_OBI',cycle=cycle+9+int(not we),port=port,seq=seq,event='RESP',data=0)
        ch, response = ('AW','B') if we else ('AR','R')
        axi(s,ch,cycle,cycle+1,addr,id=sid)
        axi(m,ch,cycle+3,cycle+4,addr,id=mid)
        if we:
            axi(s,'W',cycle,cycle+2)
            axi(m,'W',cycle+4,cycle+4)
        axi(m,response,cycle+5,cycle+6,id=mid)
        axi(s,response,cycle+8,cycle+9,id=sid)
        emit('ACCESS_MEM',cycle=cycle+4,port=m,we=we,addr=addr,data=0)
        if target==3:
            emit('ACCESS_WRAPPER',cycle=cycle+4,we=we,addr=addr,data=0)
    for sample in range(2):
        base = sample*8000
        emit('ACCESS_ID',cycle=base,pc=0x80001000,insn=0)
        for i in range(320):
            cycle = base+2+i*20
            transaction('D',0x70001000+4*i,1,cycle)
            transaction('I',0x80001004,0,cycle+3)
            emit('ACCESS_ID',cycle=cycle+15,pc=0x80001004,insn=0)
        emit('ACCESS_ID',cycle=base+6400,pc=0x80000010,insn=0)
        emit('ACCESS_ID',cycle=base+6500,pc=0x80001800,insn=0)
        for i in range(4):
            cycle = base+6502+i*40
            transaction('D',0x70000010+4*i,0,cycle)
            transaction('I',0x80001804,0,cycle+3)
        # Helpers inside read_result must remain in the read window.
        emit('ACCESS_ID',cycle=base+6690,pc=0x80001a00,insn=0)
        emit('ACCESS_ID',cycle=base+6700,pc=0x80000010,insn=0)
        lines.append(f'KWS_SAMPLE index={sample} raw=0,0,0,0 prediction=0 input=6500 compute=3000 read=210 perf=2805 total=10000')
    lines.append('KWS_PASS count=2')
    symbols = '80000010 T main\n80001000 T kws_load_input\n80001800 T kws_read_result\n80001a00 T kws_scale\n'
    return '\n'.join(lines)+'\n', symbols


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', default='build/access_profile_selftest')
    args = parser.parse_args()
    out = Path(args.output).resolve()
    assert out.is_relative_to(ROOT/'build') and out != ROOT/'build'
    out.mkdir(parents=True,exist_ok=True)
    text, symbols = fixture()
    rows, phases, iterations, incomplete = analyze(text,symbols)
    assert len(rows)==1296 and len(phases)==4 and len(iterations)==648 and not incomplete
    assert phases[0]['fetches']==320 and phases[0]['fetch_pending']==3200
    assert phases[0]['data_pending']==2880 and phases[0]['overlap']==1920
    assert phases[0]['fetch_only']==1280 and phases[0]['data_only']==960
    assert phases[1]['cycles']==200 and phases[1]['id_accepts']==2
    r=rows[0]
    assert r['req_to_resp']==9 and r['xbar_address']==2 and r['xbar_response']==2
    assert r['target_response_wait']==1 and r['adapter_response_wait']==1
    assert r['aw_w_join']==0 and r['xbar_w']==2
    checks=1
    unified, unified_symbols = fixture(schema=2)
    unified_rows, unified_phases, unified_iterations, unified_incomplete = analyze(unified, unified_symbols)
    assert len(unified_rows) == len(rows) and len(unified_phases) == len(phases)
    assert len(unified_iterations) == len(iterations) and not unified_incomplete
    checks += 1
    def reject(bad, syms=symbols):
        nonlocal checks
        try:
            analyze(bad,syms)
        except (ValueError, KeyError):
            checks+=1
            return
        raise AssertionError('invalid constructed trace accepted')
    reject(text.replace('KWS_PASS count=2',''))
    reject(text+'FAIL: injected\n')
    reject(text.replace('resp=0','resp=2',1))
    reject(text.replace('len=0','len=1',1))
    reject(text.replace('ACCESS_META schema=1','ACCESS_META schema=9'))
    reject(text.replace('event=RESP data=00000000','event=GNT data=00000000',1))
    reject(text.replace('ACCESS_WRAPPER cycle=6','ACCESS_WRAPPER cycle=7',1))
    reject(text.replace('pc=80001000','pc=80001004',1))
    reject(text.replace('channel=AW seq=0 event=H addr=70001000',
                        'channel=AW seq=0 event=H addr=70001004',1))
    reject(text, symbols.replace('kws_load_input','unknown'))
    (out/'constructed.log').write_text(text)
    (out/'symbols.txt').write_text(symbols)
    (out/'workload.hex').write_bytes(b'00000000\n')
    print(f'PASS: {checks} constructed-log checks; occupancy, FIFO pairing, W skew, backpressure, helpers and corruption rejection')
    print(f'Fixture only, never measured: {out}')


if __name__=='__main__':
    main()
