"""ABI 4 real-board supplement: new descriptor errors and burst boundaries."""
import argparse
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'software/host'),str(ROOT/'hardware/npu/scripts')]
from nocpu_jtag import Npu, Op, TclRpc, JtagTransport
from npu_program import Package, compile_model, reference, encode_tensor
from run_program_regression import fixtures
sys.path.insert(0, str(ROOT/'verification/fpga/board'))
from nocpu_board_suite import raw_upload, expect_error


def targets():
    by_name={name:model for name,model,_ in fixtures()}
    base=compile_model(by_name['cnn'])
    for label,offset,value,error in [
        ('old_abi',60,0,18),('wrong_cols',60,0x49340f44,18),
        ('reserved_shift',struct.unpack_from('<I',base.image,32)[0]+4,0x80000000,22)]:
        image=bytearray(base.image);struct.pack_into('<I',image,offset,value)
        yield label,Package(bytes(image),dict(base.manifest)),error
    for label,fixture,pc,word,change,error in [
        ('dw_channels','dwconv',0,4,lambda w:(12<<16)|12,18),
        ('pool_window','pool_general',0,5,lambda w:(w&0xffff0000)|200|(3<<8),19),
        ('add_src2_overlap','residual_add',1,11,None,20),
        ('add_fused_pool','residual_add',1,0,lambda w:w|(1<<10),18),
        ('u4_int32','host_i32_pool',0,0,lambda w:w|(1<<15),18),
        ('dw_on_fc','mlp',0,0,lambda w:w|(1<<13),18),
        ('pool1d_unfused','cnn',0,0,lambda w:(w&~(1<<10))|(1<<14),18)]:
        pkg=compile_model(by_name[fixture]);image=bytearray(pkg.image)
        value=struct.unpack_from('<I',image,pc*64+word*4)[0]
        value=(struct.unpack_from('<I',image,pc*64+8)[0]+4) if change is None else change(value)
        struct.pack_into('<I',image,pc*64+word*4,value&0xffffffff)
        yield label,Package(bytes(image),dict(pkg.manifest)),error


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--plan-only',action='store_true')
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    cases=list(targets())
    for label,pkg,_ in cases: pkg.save(args.output/'negative_packages'/label)
    if args.plan_only:
        print('Prepared',len(cases),'ABI 4 negative packages');return
    report=dict(passed=False,negative_cases=[],burst_checks=[])
    rpc=TclRpc(timeout=30);original=rpc.execute
    def logged(command):
        result=original(command)
        with (args.output/'commands.jsonl').open('a') as f:
            f.write(json.dumps(dict(time=time.time(),command=command,result=result))+'\n')
        return result
    rpc.execute=logged
    try:
        tx=JtagTransport(rpc,timeout=30);npu=Npu(tx);npu.command(Op.RESET)
        for label,pkg,error in cases:
            raw_upload(npu,pkg)
            message=expect_error(lambda:npu.command(Op.MODEL_COMMIT),error)
            report['negative_cases'].append(dict(name=label,error=error,actual=message,passed=True))
            npu.command(Op.RESET)
        for label,operation,address,error in [('unaligned_read',Op.READ,3,3),
            ('unmapped_read',Op.READ,0xfff0,2),('unknown_opcode',0x7f,0,1)]:
            message=expect_error(lambda:npu.command(operation,address),error)
            report['negative_cases'].append(dict(name=label,error=error,actual=message,passed=True))
            npu.command(Op.RESET)
        # Full SRAM and 64-word RPC chunk boundaries; verify before computation.
        _,model,frame=next(x for x in fixtures() if x[0]=='full_activation_8k')
        pkg=compile_model(model,cols=npu.read(0x58)&65535);pkg.save(args.output/'bulk_package')
        npu.load_model(pkg);inp=encode_tensor(frame,'i4')
        words=[int.from_bytes(inp[i:i+4],'little') for i in range(0,len(inp),4)]
        npu.command(Op.INPUT_BEGIN);npu.write_block(0x20000+pkg.manifest['input_base'],words)
        npu.command(Op.INPUT_COMMIT)
        readback=npu.read_block(0x20000+pkg.manifest['input_base'],len(words))
        if readback!=words: raise RuntimeError('8KiB burst read/write mismatch')
        report['burst_checks'].append(dict(name='8KiB_roundtrip',words=len(words),passed=True))
        npu.command(Op.START);deadline=time.monotonic()+30
        while not npu.read(4)&8:
            if time.monotonic()>deadline: raise TimeoutError('bulk job timeout')
        expected=encode_tensor(reference(model,frame)[model['nodes'][-1]['output']],pkg.manifest['output_dtype'])
        actual=b''.join(w.to_bytes(4,'little') for w in npu.read_block(
            0x20000+pkg.manifest['output_base'],(len(expected)+3)//4))[:len(expected)]
        for label,payload in [('input',inp),('expected',expected),('actual',actual)]:
            (args.output/f'{label}.bin').write_bytes(payload)
        if actual!=expected: raise RuntimeError('bulk inference mismatch')
        npu.command(Op.ACK)
        # Last valid word followed by first out-of-range word: error must not
        # become a successful stale read, and the tail address must be correct.
        try: npu.read_block(0x21ffc,2)
        except RuntimeError as exc:
            if 'JTAG burst fault' not in str(exc): raise
        else: raise RuntimeError('invalid burst tail was accepted')
        response=tx.scan(0)
        if (response>>64)&0xff!=0xa2 or (response>>32)&0xffffffff!=0x22000:
            raise RuntimeError(f'incorrect burst error/address: {response:x}')
        report['burst_checks'].append(dict(name='out_of_range_tail',response_hex=f'{response:018x}',passed=True))
        npu.command(Op.RESET)
        _,small,values=next(fixtures());small_pkg=compile_model(small)
        npu.load_model(small_pkg)
        result=npu.infer(encode_tensor(values,'i4'))
        golden=encode_tensor(reference(small,values)[small['nodes'][-1]['output']],small_pkg.manifest['output_dtype'])
        if result!=golden: raise RuntimeError('burst fault recovery mismatch')
        report['burst_checks'].append(dict(name='fresh_inference_after_burst_fault',passed=True))
        npu.command(Op.RESET);report['final_status']=npu.status();report['passed']=True
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');rpc.close()
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
