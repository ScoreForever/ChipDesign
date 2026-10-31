#!/usr/bin/env python3
"""ABI 3 INT4 end-to-end operator/compiler/controller/JTAG regression (Icarus)."""
from pathlib import Path
import argparse
import hashlib
import json
import random
import re
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'software/host'))
from npu_program import compile_model,reference,Package,requant,from_tinycnn_npz,ABI,pack_i4,quantize_int8_model
from nocpu_jtag import Npu,Op

def fixtures(seed=7):
    r=random.Random(seed)
    def mac(op,src,dst,k,oc,**kw):
        return dict(op=op,input=src,output=dst,channels=oc,weights=[r.randrange(-8,8) for _ in range(k*oc)],
                    bias=[r.randrange(-50,51) for _ in range(oc)],**kw)
    def q(src,dst,c,shift=-3):return dict(op='Requant',input=src,output=dst,multiplier=[0x4000]*c,shift=[shift]*c)
    a=dict(input=dict(name='x',shape=[4,6,3]),nodes=[
        mac('Conv','x','a',27,5,kernel=3,pads=[1]*4),q('a','b',5),
        dict(op='ReLU',input='b',output='c'),dict(op='MaxPool',input='c',output='d'),
        mac('FC','d','e',30,11),q('e','f',11),dict(op='ReLU',input='f',output='y')])
    b=dict(input=dict(name='x',shape=[5,7,3]),nodes=[
        dict(op='ReLU',input='x',output='a'),dict(op='MaxPool',input='a',output='b'),q('b','c',3,0),
        mac('Conv','c','d',3,9,kernel=1),q('d','e',9),mac('FC','e','y',54,2)])
    c=dict(input=dict(name='x',shape=[1,1,7]),nodes=[
        mac('FC','x','a',7,13),dict(op='ReLU',input='a',output='b'),q('b','c',13),
        mac('FC','c','y',13,3)])
    d=dict(input=dict(name='x',shape=[4,4,1]),nodes=[
        mac('Conv','x','a',1,3,kernel=1),dict(op='MaxPool',input='a',output='b'),
        dict(op='ReLU',input='b',output='c'),q('c','y',3)])
    e=dict(input=dict(name='x',shape=[3,5,5]),nodes=[
        mac('Conv','x','a',45,10,kernel=3,stride=[2,1],pads=[1,0,1,2]),q('a','b',10),
        mac('Conv','b','c',10,3,kernel=1),q('c','y',3)])
    edge=dict(input=dict(name='x',shape=[1,1,9]),nodes=[
        dict(op='FC',input='x',output='a',channels=9,weights=[(-8 if i%2 else 7) for i in range(81)],
             bias=[-2**31,2**31-1,-1,0,1,-2**31+5,2**31-6,123456789,-123456789]),
        dict(op='Requant',input='a',output='y',multiplier=[0,1,0x7fff,0x4000,0x6000,0x7fff,123,0x5000,0],
             shift=[31,-31,0,-1,1,-12,12,5,-5])])
    long=dict(input=dict(name='x',shape=[1,1,257]),nodes=[mac('FC','x','y',257,9)])
    long['nodes'][0]['weights']=[-8]*(257*9)
    for name,model in [('cnn',a),('pool_first',b),('mlp',c),('i32_pool',d),('strided',e),('edges',edge),('long_k',long)]:
        frame=[r.randrange(-8,8) for _ in range(__import__('math').prod(model['input']['shape']))]
        if name=='long_k':frame=[-8]*257
        yield name,model,frame
    nodes=[];src='x'
    for idx,c in ((1,1),(2,8)):
        dst=f'c{idx}'
        nodes.extend([mac('Conv',src,dst,9*c,8,kernel=3,pads=[1]*4),q(dst,f'q{idx}',8),
            dict(op='ReLU',input=f'q{idx}',output=f'r{idx}'),dict(op='MaxPool',input=f'r{idx}',output=f'p{idx}')])
        src=f'p{idx}'
    nodes.append(mac('FC',src,'y',160,8))
    yield 'tinycnn',dict(input=dict(name='x',shape=[20,16,1]),nodes=nodes),[r.randrange(-8,8) for _ in range(320)]
    yield 'max_k_int4',dict(input=dict(name='x',shape=[1,1,1024]),nodes=[
        dict(op='FC',input='x',output='y',channels=1,weights=[-8]*1024,bias=[0])]),[-8]*1024
    yield 'odd_packed_relu',dict(input=dict(name='x',shape=[1,1,17]),nodes=[
        dict(op='ReLU',input='x',output='y')]),[-8,-7,-1,0,1,6,7]*2+[-8,0,7]
    yield 'host_i32_quant',dict(input=dict(name='x',shape=[3,5,3],dtype='i32'),nodes=[
        dict(op='Requant',input='x',output='a',multiplier=[0x4000,0x7fff,0],shift=[-4,1,-31]),
        dict(op='ReLU',input='a',output='b'),dict(op='MaxPool',input='b',output='y')]),[r.randrange(-2**31,2**31) for _ in range(45)]
    yield 'host_i32_pool',dict(input=dict(name='x',shape=[4,4,2],dtype='i32'),nodes=[
        dict(op='MaxPool',input='x',output='a'),dict(op='ReLU',input='a',output='y')]),[r.randrange(-2**31,2**31) for _ in range(32)]
    yield 'tiny_padded',dict(input=dict(name='x',shape=[1,1,1]),nodes=[
        dict(op='Conv',input='x',output='a',channels=1,kernel=3,pads=[2]*4,weights=[-4,-3,-2,-1,0,1,2,3,4],bias=[-7]),
        dict(op='MaxPool',input='a',output='y')]),[-8]
    # One-pixel, stride-two asymmetric padding exercises empty tap ranges
    # (extent+padding-tap <= 0) and rounded-up lower/upper coordinate bounds.
    yield 'tiny_strided_asymmetric',dict(input=dict(name='x',shape=[1,1,1]),nodes=[
        dict(op='Conv',input='x',output='y',channels=2,kernel=3,stride=[2,2],
             pads=[0,2,2,0],weights=[(-8+i%16) for i in range(18)],bias=[-7,9])]),[-8]
    yield 'one_mac',dict(input=dict(name='x',shape=[1,1,1]),nodes=[
        dict(op='FC',input='x',output='y',channels=1,weights=[-8],bias=[2**31-1])]),[-8]
    yield 'negative_pool',dict(input=dict(name='x',shape=[3,3,1]),nodes=[
        dict(op='MaxPool',input='x',output='y')]),[-8,-7,-6,-5,-4,-3,-2,-1,-1]
    yield 'full_activation',dict(input=dict(name='x',shape=[64,128,1]),nodes=[
        dict(op='ReLU',input='x',output='y')]),[r.randrange(-8,8) for _ in range(8192)]
    # Keep the old exact-4KiB fixture and explicitly cover the expanded SRAM
    # high address bit, including full-capacity in-place execution.
    yield 'full_activation_8k',dict(input=dict(name='x',shape=[128,128,1]),nodes=[
        dict(op='ReLU',input='x',output='y')]),[r.randrange(-8,8) for _ in range(16384)]
    yield 'upper_activation',dict(input=dict(name='x',shape=[32,32,1],dtype='i32'),nodes=[
        q('x','a',1,-5),dict(op='ReLU',input='a',output='y')]),[r.randrange(-2**31,2**31) for _ in range(1024)]
    # Fast 1x1 producer versus eight-lane postprocessing deliberately reaches
    # the four reserved result slots and must report scheduler backpressure.
    yield 'slot_pressure',dict(input=dict(name='x',shape=[8,8,1]),nodes=[
        dict(op='Conv',input='x',output='a',channels=8,kernel=1,
             weights=[1,-1,2,-2,3,-3,4,-4],bias=[0]*8),
        q('a','y',8,-2)]),[i%16-8 for i in range(64)]

class Recorder:
    """Records the real host API. Expected data comes only from logical oracle."""
    def __init__(self,pkg,output):
        self.rows=[];self.regs={0:ABI,0x50:8192,0x54:8192,0x58:(4<<16)|pkg.manifest['cols'],0x5c:1024,0x60:16,0x6c:0x0f200404}
        self.output=output;self.started=False;self.completed=False;self.pkg=pkg
        self.check_live_perf=False
    def exchange(self,op,addr=0,data=0):
        value=0;mask=0xffffffff
        if op==Op.WRITE and addr<0x10000:self.regs[addr]=data
        if op==Op.START:
            self.started=True;value=1;mask=0 # job id increases across repeated inference
        if op==Op.READ:
            if addr==4:
                if self.started:
                    self.rows.append('f0 0 0 0 0 0');value=10;mask=0x49;self.completed=True
                else:value=2;mask=0
            elif 0x20000<=addr<0x22000:
                at=addr-0x20000-self.pkg.manifest['output_base']
                chunk=self.output[at:at+4];value=int.from_bytes(chunk.ljust(4,b'\0'),'little');mask=(1<<(8*len(chunk)))-1
            else:value=self.regs.get(addr,0)
        if op==Op.ACK:self.started=False
        self.rows.append(f'{int(op):02x} {addr:08x} {int(data)&0xffffffff:08x} 00 {mask:08x} {value:08x}')
        if op==Op.START and self.check_live_perf:
            self.rows.extend(['01 00000100 0 04 0 0','01 00000194 0 04 0 0','0a 0 0 0 0 0'])
        return value

def encoded(values,dtype):
    return struct.pack('<'+'i'*len(values),*values) if dtype=='i32' else pack_i4(values)

def run(command,log):
    try:
        p=subprocess.run([str(x) for x in command],cwd=ROOT,text=True,stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT,timeout=600 if str(command[0])=='vvp' else 240)
    except subprocess.TimeoutExpired as error:
        output=error.stdout or ''
        if isinstance(output,bytes):output=output.decode(errors='replace')
        log.write_text(output,encoding='utf-8')
        raise RuntimeError(f'host process timeout; preserved partial output in {log}') from error
    log.write_text(p.stdout,encoding='utf-8')
    if p.returncode or 'FATAL:' in p.stdout:raise RuntimeError(f'{command[0]} failed: {log}\n{p.stdout[-5000:]}')
    return p.stdout

def negative_tests(out,image):
    name,model,frame=next(fixtures());base=compile_model(model)
    mutations=[('opcode',0,99,18),('reserved',44,1,18),('old_abi',60,0,18),('wrong_cols',60,0x49340f44,18),('stride',20,0,18),
               ('shape',40,0,19),('destination',8,8188,20),('source',4,2048,21),
               ('multiplier',struct.unpack_from('<I',base.image,32)[0]+4,0x00008000,22),
               ('shift',struct.unpack_from('<I',base.image,32)[0]+4,32<<16,22)]
    results=[]
    for name,offset,value,error in mutations:
        data=bytearray(base.image);struct.pack_into('<I',data,offset,value)
        pkg=Package(bytes(data),base.manifest);rec=Recorder(pkg,b'');host=Npu(rec);host.load_model(pkg)
        parts=rec.rows[-1].split();parts[3]=f'{error:x}';rec.rows[-1]=' '.join(parts)
        rec.rows.extend(['01 40 0 0 ffffffff 0','07 0 0 5 0 0','09 0 0 0 0 0'])
        trace=out/f'negative_{name}.txt';trace.write_text('\n'.join(rec.rows)+'\n')
        run(['vvp',image,f'+TRACE={trace.relative_to(ROOT).as_posix()}'],out/f'negative_{name}.log')
        results.append(name);print('PASS reject',name,flush=True)
    # Exercise lifecycle, transfer completeness, result protection, abort, and
    # timeout with a real valid program, not a behavioral controller stub.
    expected=reference(model,frame)[model['nodes'][-1]['output']]
    rec=Recorder(base,encoded(expected,base.manifest['output_dtype']));host=Npu(rec);host.load_model(base)
    def c(op,addr=0,data=0,error=0,mask=0,value=0):rec.rows.append(f'{op:x} {addr:x} {data:x} {error:x} {mask:x} {value:x}')
    c(7,error=5);c(6,error=5);c(2,0x20000,error=5)
    c(5);c(6,error=7);c(2,0x20004,error=6)
    # Use the real host sequence, then insert commands immediately after START.
    before=len(rec.rows);host.infer(pack_i4(frame))
    start=next(i for i in range(before,len(rec.rows)) if rec.rows[i].startswith('07 '))
    rec.rows[start+1:start+1]=['07 0 0 4 0 0','02 10000 0 4 0 0','01 20000 0 4 0 0']
    ack=next(i for i in range(start,len(rec.rows)) if rec.rows[i].startswith('08 '))
    rec.rows[ack:ack]=['03 0 0 9 0 0','05 0 0 9 0 0']
    # Start another inference, abort it, and ensure no stale done arrives.
    old=len(rec.rows);host.infer(pack_i4(frame))
    start=next(i for i in range(old,len(rec.rows)) if rec.rows[i].startswith('07 '))
    rec.rows=rec.rows[:start+1]
    c(9);c(0xf1,data=100);c(1,4,mask=0x7f,value=0);c(7,error=5)
    host.load_model(base)
    old=len(rec.rows);host.infer(pack_i4(frame))
    start=next(i for i in range(old,len(rec.rows)) if rec.rows[i].startswith('07 '))
    rec.rows=rec.rows[:start]+['02 28 2 0 0 0']+rec.rows[start:start+1]
    c(0xf0);c(1,8,mask=0xffffffff,value=10);c(1,4,mask=0x49,value=0x40);c(9);c(2,0x28,10000000)
    # Model upload count is transactional: one missing word cannot commit.
    rec2=Recorder(base,b'');h2=Npu(rec2);h2.load_model(base)
    rec.rows.extend(rec2.rows[:-2]);c(4,error=7);c(9)
    trace=out/'lifecycle.txt';trace.write_text('\n'.join(rec.rows)+'\n')
    run(['vvp',image,f'+TRACE={trace.relative_to(ROOT).as_posix()}'],out/'lifecycle.log')
    print('PASS lifecycle/abort/timeout/incomplete',flush=True)
    return results+['lifecycle']

def unit_tests(out,defines,macro_model=None):
    for cols in (4,8):
        image=out/f'mac_int4_{cols}.vvp'
        run(['iverilog','-g2012',*(['-DCOLS4'] if cols==4 else []),'-s','tb_npu_mac_int4','-o',image,
             'hardware/npu/rtl/npu_mac_array.sv','hardware/npu/tb/tb_npu_mac_int4.sv'],out/f'mac_int4_{cols}_compile.log')
        run(['vvp',image],out/f'mac_int4_{cols}.log')
    sys.path.insert(0,str(ROOT/'hardware/npu/tools'))
    r=random.Random(20261023);rows=[]
    edges=[-2**31,2**31-1,-1,0,1,7,-129,65537,-65537]
    for i in range(20000):
        x=edges[i%len(edges)] if i%3==0 else r.randrange(-2**31,2**31)
        m=r.choice([0,1,0x7fff,0x4000,r.randrange(0,2**15)])
        shift=r.randrange(-31,32);lo,hi=r.choice([(-8,7),(0,7),(-4,3)])
        sx=max(-2**31,min(2**31-1,x*(1<<max(shift,0))))
        scaled=(sx*m+(1<<14))//(1<<15)
        if shift<0:
            divisor=1<<-shift;quotient,remainder=divmod(scaled,divisor)
            scaled=quotient+int(2*remainder>divisor or (2*remainder==divisor and scaled>=0))
        expected=max(lo,min(hi,scaled))
        assert requant(x,m,shift,lo,hi)==expected
        rows.append(f'{x&0xffffffff:08x} {m&0xffffffff:08x} {shift&63:02x} {lo&255:02x} {hi:02x} {expected&15:01x}')
    vectors=out/'quant.hex';vectors.write_text('\n'.join(rows)+'\n')
    qimage=out/'quant.vvp'
    run(['iverilog','-g2012','-s','tb_requant_lane','-o',qimage,
         'hardware/npu/rtl/requant_lane.sv','hardware/npu/tb/tb_requant_lane.sv'],out/'quant_compile.log')
    run(['vvp',qimage,f'+VECTORS={vectors.relative_to(ROOT).as_posix()}'],out/'quant.log')
    memimage=out/'memories.vvp'
    extra=[str(macro_model.resolve())] if macro_model else []
    run(['iverilog','-g2012',*defines,'-s','tb_npu_memories','-o',memimage,
         'hardware/npu/rtl/npu_model_store.sv','hardware/npu/rtl/npu_activation_store.sv',
         'hardware/npu/tb/tb_npu_memories.sv',*extra],out/'memories_compile.log')
    run(['vvp',memimage],out/'memories.log')
    pressure_image=out/'activation_pressure.vvp'
    run(['iverilog','-g2012',*defines,'-s','tb_npu_activation_pressure','-o',pressure_image,
         'hardware/npu/rtl/npu_activation_store.sv','hardware/npu/tb/tb_npu_activation_pressure.sv',*extra],
        out/'activation_pressure_compile.log')
    run(['vvp',pressure_image],out/'activation_pressure.log')
    timage=out/'trace.vvp'
    run(['iverilog','-g2012','-s','tb_npu_trace','-o',timage,
         'hardware/npu/rtl/npu_trace_capture.sv','hardware/npu/tb/tb_npu_trace.sv'],out/'trace_compile.log')
    run(['vvp',timage],out/'trace.log')
    simage=out/'stalls.vvp'
    run(['iverilog','-g2012','-s','tb_npu_stalls','-o',simage,
         'hardware/npu/rtl/npu_mac_array.sv','hardware/npu/rtl/requant_lane.sv',
         'hardware/npu/rtl/npu_program_core.sv','hardware/npu/tb/tb_npu_stalls.sv'],out/'stalls_compile.log')
    stall_cases=[(name,model,frame,fuse) for name,model,frame in fixtures()
                 if name in ('cnn','slot_pressure','upper_activation')
                 for fuse in ((True,False) if name=='cnn' else (True,))]
    for name,model,frame,fuse in stall_cases:
        pkg=compile_model(model,fuse=fuse);m=pkg.manifest;case=out/f'stalls_{name}_{fuse}';case.mkdir(exist_ok=True)
        def hx(file,values,width):file.write_text(''.join(f'{v:0{width}x}\n' for v in values))
        hx(case/'config.hex',[m[k] for k in ('model_bytes','op_count','input_base','input_bytes','output_base','output_bytes')]+[int(m['input_dtype']=='i32')],8)
        hx(case/'model.hex',struct.unpack('<'+'I'*(len(pkg.image)//4),pkg.image),8)
        hx(case/'input.hex',encoded(frame,m['input_dtype']),2)
        hx(case/'gold.hex',encoded(reference(model,frame)[model['nodes'][-1]['output']],m['output_dtype']),2)
        run(['vvp',simage,f'+DATA={case.relative_to(ROOT).as_posix()}'],case/'rtl.log')
    print('PASS 20000 quant vectors, dual-SRAM memory semantics, trace, random read/write stalls and drain',flush=True)

def simulate(out,image,name,model,frame,cols,fuse,jtag,wave_disabled=False):
    """Run one compiled model through the real host API on an RTL image; check every tensor write."""
    case=out/f'{name}_c{cols}_f{int(fuse)}_j{int(jtag)}';case.mkdir(exist_ok=True)
    pkg=compile_model(model,cols=cols,fuse=fuse);pkg.save(case)
    logical=reference(model,frame);expected=logical[model['nodes'][-1]['output']]
    output=encoded(expected,pkg.manifest['output_dtype']);rec=Recorder(pkg,output)
    rec.check_live_perf=not jtag
    host=Npu(rec)
    host.load_model(pkg)
    for _ in range(2):
        got=host.infer(encoded(frame,model['input'].get('dtype','i4')))
        assert got==output
        rec.rows.extend(['01 00000038 0 0 0 0','01 00000040 0 0 0 0'])
        rec.rows.append('01 00000068 0 0 ffffffff 00010018')
        rec.rows.extend(f'01 {0x100+4*i:08x} 0 0 0 0' for i in range(24))
        rec.rows.extend(['01 00000180 0 0 ffffffff 00030080',
                         f'01 00000184 0 0 ffffffff {0 if wave_disabled else 1024:08x}'])
        rec.rows.extend(f'01 {addr:08x} 0 0 0 0' for addr in range(0x190,0x1a0,4))
        rec.rows.extend(f'01 {0x200+4*i:08x} 0 0 0 0' for i in range(pkg.manifest['op_count']*8))
        # Exercise the synchronous RAM port through both transports.
        rec.rows.extend(f'01 {0x1000+4*i:08x} 0 {2 if wave_disabled else 0:x} 0 0' for i in range(8))
    trace=case/'commands.txt';trace.write_text('\n'.join(rec.rows)+'\n')
    # Check every materialized tensor write, including values that
    # are subsequently overwritten through lifetime reuse.
    events=[]
    for tensor,info in list(pkg.manifest['tensors'].items())[1:]:
        values=logical[tensor];channels=info['shape'][2]
        for oc in range(0,channels,cols):
            for pos in range(len(values)//channels):
                for lane in range(min(cols,channels-oc)):
                    index=pos*channels+oc+lane;wide=info['dtype']=='i32'
                    address=info['base']*2+index*(8 if wide else 1)
                    mask=255 if wide else (3 if len(values)%2 and index==len(values)-1 else 1)
                    value=values[index] & (0xffffffff if wide else 15)
                    events.append((address<<48)|(mask<<32)|value)
    events*=2;event_file=case/'writes.hex';event_file.write_text(''.join(f'{v:016x}\n' for v in events))
    log=run(['vvp',image,f'+TRACE={trace.relative_to(ROOT).as_posix()}',
             f'+EVENTS={event_file.relative_to(ROOT).as_posix()}',f'+EVENT_COUNT={len(events)}'],case/'rtl.log')
    if 'PASS program' not in log:raise RuntimeError(log)
    metrics=re.findall(r'METRIC reg=([0-9a-f]+) value=(\d+)',log)
    row=dict(case=case.name,commands=len(rec.rows),bytes=len(pkg.image),peak=pkg.manifest['activation_peak'],groups=pkg.manifest['groups'],
             cycles=[int(v) for a,v in metrics if int(a,16)==0x38],writes=[int(v) for a,v in metrics if int(a,16)==0x40],checked_write_events=len(events))
    perf=re.findall(r'PERF reg=([0-9a-f]+) value=(\d+)',log)
    assert len(perf)==48,case
    banks=[[int(v) for _,v in perf[i:i+24]] for i in (0,24)]
    for n,bank in enumerate(banks):
        assert bank[0]==row['cycles'][n] and bank[11]==row['writes'][n],case
        assert bank[1]==bank[4] and bank[6]==bank[9],case
        assert bank[3]<=bank[2] and bank[8]<=bank[7],case
        assert bank[17]<=4 and bank[18]<=2 and bank[19]<=32,case
    # SRAM read-cache contents persist across jobs. Latencies,
    # stalls and peak occupancy may differ on the warm second run;
    # deterministic operator work must repeat without accumulation.
    work_indices=(1,4,6,9,11,12,16,20,21,22,23)
    assert all(banks[0][i]==banks[1][i] for i in work_indices),f'{case}: work counters did not reset/repeat'
    if name=='slot_pressure' and cols==8:
        assert banks[0][17]==4 and banks[0][15]>0,case
    row['performance']=banks
    op_values=[int(v) for _,v in re.findall(r'OPTRACE reg=([0-9a-f]+) value=(\d+)',log)]
    n=pkg.manifest['op_count'];assert len(op_values)==n*8*2,case
    op_banks=[[op_values[j:j+8] for j in range(at,at+n*8,8)] for at in (0,n*8)]
    for run_index,ops in enumerate(op_banks):
        assert ops[0][0]==0 and ops[-1][1]==row['cycles'][run_index],case
        assert all(x[3]==11 and x[1]>x[0] for x in ops),case
        assert all(x[1]==y[0] for x,y in zip(ops,ops[1:])),case
        assert all(x[2]==struct.unpack_from('<I',pkg.image,pc*64)[0] for pc,x in enumerate(ops)),case
        for index,perf_index in ((4,13),(5,14),(6,15),(7,12)):
            assert sum(x[index] for x in ops)==banks[run_index][perf_index],case
    assert all(x[2]==y[2] and x[7]==y[7] for x,y in zip(*op_banks)),case
    hdr=[int(v) for _,v in re.findall(r'TRACEHDR reg=([0-9a-f]+) value=(\d+)',log)]
    for run_index,at in enumerate((0,4)):
        duration=op_banks[run_index][0][1];expected_count=min(1024,duration)
        expected_header=([4,0,0,0] if wave_disabled else
                         [5|(2 if duration>=1024 else 0)|(8 if duration>1024 else 0),expected_count,0,expected_count-1])
        assert hdr[at:at+4]==expected_header,case
    row['operator_trace']=op_banks
    return row

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,default=ROOT/'build/program_regression')
    ap.add_argument('--quick',action='store_true');ap.add_argument('--macro-model',type=Path)
    ap.add_argument('--wave-disabled',action='store_true',help='exercise ASIC-style disabled waveform trace, preserving operator summaries')
    ap.add_argument('--trained-model',type=Path,help='optional real trained NPZ; needs NumPy')
    ap.add_argument('--define',action='append',default=[],help='extra Verilog define, e.g. NPU_ENABLE_WAVE_TRACE=0')
    args=ap.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    sources=(ROOT/'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
    defines=['-D'+d for d in args.define]
    if args.wave_disabled:defines.append('-DNPU_ENABLE_WAVE_TRACE=0')
    if args.macro_model:
        sources.append(str(args.macro_model.resolve()))
        defines.extend(['-DNPU_USE_RA1SHD_MODEL','-DNPU_USE_RA1SHD_ACTIVATION'])
    unit_tests(out,defines,args.macro_model)
    cases=list(fixtures())
    if args.trained_model:
        rng=random.Random(20261023)
        cases.append(('trained',from_tinycnn_npz(args.trained_model),[rng.randrange(-8,8) for _ in range(320)]))
    results=[];variants=[(8,False),(4,False),(8,True)] if not args.quick else [(8,False)]
    negatives=[]
    for cols,jtag in variants:
        image=out/f'program_{cols}_{jtag}.vvp'
        ds=defines+(['-DCOLS4','-DNPU_ARRAY_COLS=4'] if cols==4 else [])+(['-DJTAG_TEST'] if jtag else [])
        run(['iverilog','-g2012','-s','tb_npu_program','-o',image,*ds,*sources,'hardware/soc/sim/tb/tb_npu_program.sv'],out/f'compile_{cols}_{jtag}.log')
        for name,model,frame in cases:
            if jtag and name!='cnn':continue
            for fuse in (True,False):
                if name in ('tinycnn','trained') and not fuse:continue # raw Conv alone needs 10240 B, exceeding 8 KiB
                row=simulate(out,image,name,model,frame,cols,fuse,jtag,args.wave_disabled)
                results.append(row);print('PASS',row['case'],flush=True)
        if cols==8 and not jtag:negatives=negative_tests(out,image)
    # A common post chain must demonstrate physical savings, not only equal outputs.
    cnn={r['case']:r for r in results if r['case'].startswith('cnn_c8_') and r['case'].endswith('j0')}
    if cnn:
        fused=cnn['cnn_c8_f1_j0'];plain=cnn['cnn_c8_f0_j0']
        assert fused['writes'][0]<plain['writes'][0] and fused['cycles'][0]<plain['cycles'][0]
    (out/'summary.json').write_text(json.dumps(dict(tests=results,negative_tests=negatives,sources_sha256={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources if not Path(s).is_absolute()}),indent=2)+'\n')
    print(f'ALL {len(results)} PROGRAM TESTS PASSED',flush=True)
if __name__=='__main__':main()
