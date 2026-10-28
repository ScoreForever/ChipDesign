#!/usr/bin/env python3
"""Check optional waveform elaboration, six build configurations and host API.

Run from the repository root with Python and Icarus Verilog in PATH. Generated
logs, VVP images and source fingerprints stay in build/. This is RTL elaboration
and simulation evidence, not mapped ASIC area or physical validation.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
RTL=ROOT/'hardware/npu/rtl/npu_trace_capture.sv'
TB=ROOT/'hardware/npu/tb/tb_npu_trace_configuration.sv'
VARIANTS=(
    # name, defines, waveform enabled, per-operator summaries enabled
    ('simulation',(),1,1),
    ('asic',('-DSYNTHESIS',),0,0),
    ('fpga',('-DSYNTHESIS','-DCHIPDESIGN_FPGA_MEM'),1,1),
    ('explicit_off',('-DNPU_ENABLE_WAVE_TRACE=0',),0,1),
    ('asic_explicit_on',('-DSYNTHESIS','-DNPU_ENABLE_WAVE_TRACE=1'),1,0),
    ('fpga_explicit_off',('-DSYNTHESIS','-DCHIPDESIGN_FPGA_MEM','-DNPU_ENABLE_WAVE_TRACE=0'),0,1),
    ('asic_ops_on',('-DSYNTHESIS','-DNPU_ENABLE_OP_TRACE=1'),0,1),
    ('simulation_ops_off',('-DNPU_ENABLE_OP_TRACE=0',),1,0),
)


def run(command,log):
    result=subprocess.run(list(map(str,command)),cwd=ROOT,capture_output=True,text=True,timeout=60)
    output=result.stdout+result.stderr
    log.write_text(output,encoding='utf-8')
    if result.returncode or re.search(r'\b(?:FATAL|ERROR)\b',output):
        raise RuntimeError(f'command failed; see {log}\n{output}')
    return output


def check_host_disabled_wave(out):
    sys.path.insert(0,str(ROOT/'software/host'))
    from nocpu_jtag import Npu,Op,ABI
    from npu_trace import export_trace

    class SummaryRegisters:
        def __init__(self):
            self.reads=[]
            self.registers={0:ABI,4:10,0x2c:7,0x14:1,0x180:0x10080,
                0x184:0,0x188:1,0x18c:0,0x190:4,0x194:0,0x198:0,0x19c:0,
                0x1a0:16,0x1a4:32,0x1a8:16,0x1ac:0,0x1b0:0}
            for i,value in enumerate((10,13,0x301,11,1,1,1,1)):
                self.registers[0x200+i*4]=value
        def exchange(self,op,addr=0,data=0):
            if op!=Op.READ:raise AssertionError('summary inspection must be read-only')
            self.reads.append(addr)
            return self.registers[addr]

    registers=SummaryRegisters()
    capture=Npu(registers).trace(include_samples=False)
    if capture['header']['depth']!=0 or capture['operators'][0]['duration_cycles']!=3:
        raise AssertionError('disabled-wave operator summary changed')
    if capture['samples'] or any(address>=0x1000 for address in registers.reads):
        raise AssertionError('metadata-only inspection accessed waveform RAM')
    for call in (lambda:Npu(registers).trace(),lambda:export_trace(capture,out/'disabled.vcd')):
        try:
            call()
        except (RuntimeError,ValueError) as error:
            if 'waveform trace is not enabled' not in str(error):raise
        else:
            raise AssertionError('disabled waveform read/export succeeded')
    registers.registers[0x1a8]=0
    try:
        Npu(registers).trace(include_samples=False)
    except RuntimeError as error:
        if 'per-operator summaries are not enabled' not in str(error):raise
    else:
        raise AssertionError('disabled operator summaries were reported as available')
    return 'PASS disabled-wave host summaries, explicit waveform rejection and disabled-summary rejection'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build/trace_configuration')
    args=parser.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(ROOT/'build'):
        parser.error('--output must be inside the repository build/ directory')
    for tool in ('iverilog','vvp'):
        if not shutil.which(tool):parser.error(f'{tool} is not in PATH')
    out.mkdir(parents=True,exist_ok=True)
    results=[]
    for name,defines,wave,ops in VARIANTS:
        image=out/f'{name}.vvp'
        run(['iverilog','-g2012',*defines,'-s','tb_npu_trace_configuration',
             '-o',image,RTL,TB],out/f'{name}_compile.log')
        output=run(['vvp',image,f'+WAVE={wave}',f'+OPS={ops}'],out/f'{name}.log')
        if f'PASS waveform configuration and operator summaries WAVE={wave} OPS={ops}' not in output:
            raise AssertionError(f'{name}: missing completion marker')
        # Icarus emits arrays only for selected generate branches. Check the
        # actual elaborated VVP, rather than searching source preprocessor text.
        arrays=[name.decode('ascii') for name in
                re.findall(rb'\.array\s+"(ram[0-3])"',image.read_bytes())]
        expected={'ram0','ram1','ram2','ram3'} if wave else set()
        if set(arrays)!=expected:
            raise AssertionError(f'{name}: unexpected waveform arrays {arrays}')
        row=dict(configuration=name,defines=list(defines),wave_enabled=bool(wave),operator_summaries=bool(ops),
                 elaborated_wave_arrays=sorted(arrays),result='PASS')
        results.append(row)
        print('PASS',name,'wave='+str(wave),'ops='+str(ops),'arrays='+str(len(arrays)),flush=True)
    host=check_host_disabled_wave(out)
    (out/'host.log').write_text(host+'\n',encoding='utf-8')
    sources=(RTL,TB,Path(__file__),ROOT/'software/host/nocpu_jtag.py',ROOT/'software/host/npu_trace.py')
    summary=dict(scope='RTL elaboration/simulation and host API checks; no mapped or physical area claim',
                 configurations=results,host_disabled_wave='PASS',
                 sources_sha256={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(host,flush=True)
    print(f'ALL {len(VARIANTS)} TRACE CONFIGURATIONS PASSED',flush=True)


if __name__=='__main__':
    main()
