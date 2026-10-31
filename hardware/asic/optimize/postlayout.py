#!/usr/bin/env python3
"""Strict gate/SDF physical-JTAG acceptance; run on the course Linux node."""
from pathlib import Path
import argparse
import gzip
import json
import re
import subprocess
import time
from flow import HERE, ROOT, libraries, launch, recover_tool, metrics, save, digest

# req/ack toggle synchronizer first stages and both reset synchronizer stages
# (npu_jtag_transport.sv); payload registers are stable by the handshake.
CDC_CAPTURE=('i_host_ack_sync_reg[0]','i_host_req_sync_reg[0]','i_host_sys_reset_sync_reg[0]',
             'i_host_sys_reset_sync_reg[1]','i_host_tck_reset_sync_reg[0]','i_host_tck_reset_sync_reg[1]')

CORE_PERIOD_NS=20.0


def ifnone_arcs(model_text):
    """Default (ifnone) path arcs declared by each Verilog cell model."""
    return {m[1]:set(re.findall(r'ifnone\s*\(\s*(\w+)\s*=>\s*(\w+)\s*\)',m[2]))
            for m in re.finditer(r'(?ms)^module\s+(\w+)\b(.*?)^endmodule',model_text)}


SRAM='RA1SHD_2048x32M8'


def sram_clk_q_noedge(sdf_text, macro_text):
    """Drop the edge from the SRAM CLK->Q IOPATHs so they annotate.

    The SRAM model declares the read path without an edge, (CLK => Q[i]);
    Innovus writes it as (IOPATH (posedge CLK) Q[i] ...). Q changes only after
    the rising edge, so the values are the same arc. Only this cell and arc are
    rewritten, and only when the model really declares the unqualified path."""
    if not re.search(r'\(\s*CLK\s*=>\s*Q\[',macro_text):
        raise RuntimeError('SRAM model does not declare (CLK => Q[i]); refusing to rewrite SDF')
    count=0;out=[]
    for i,cell in enumerate(re.split(r'(?=\(CELL(?!TYPE))',sdf_text)):
        if re.search(r'\(CELLTYPE\s+"%s"'%SRAM,cell):
            cell,n=re.subn(r'\(IOPATH\s+\(posedge\s+CLK\)\s+(Q\[\d+\])',r'(IOPATH CLK \1',cell);count+=n
        out.append(cell)
    return ''.join(out),count


def sram_period_ok(macro_text):
    """The SRAM model checks the posedge CLK period only, below the core period."""
    pos=re.findall(r'\$period\s*\(\s*posedge\s+CLK\s*,\s*([\d.]+)',macro_text)
    neg=re.findall(r'\$period\s*\(\s*negedge\s+CLK',macro_text)
    return bool(pos) and not neg and all(float(v)<CORE_PERIOD_NS for v in pos)


def classify(logs, ifnone, sram_ok):
    """Fail on any violation or annotation problem except two audited classes.

    SDFNEP: Innovus writes the default arc of non-unate cells (a full adder's
    A->S) as (IOPATH (posedge A) S ...), but the model declares it as
    `ifnone (A => S)`, so it cannot annotate. The conditional arcs, which cover
    every 0/1 input state, do annotate; the default arc is used only for X
    inputs. Each unannotated path must be such a declared ifnone arc.
    SDFNET: the SRAM model checks only the posedge CLK period (3 ns < 20 ns),
    so the SDF negedge CLK PERIOD has no target.
    The simulator runs with -ignr_warnmax for both, so the lists are complete."""
    fatal=re.findall(r'(?im)^.*(?:timing violation|timing check violation|\*E,|\*F,|SDFINF|SDFCTE|SDFRDE|\*W,MXWARN).*$',logs)
    accepted={};bad_nep=[];bad_net=[]
    for line in re.findall(r'(?m)^.*\*W,SDFNEP:.*$',logs):
        m=re.search(r'IOPATH \((?:posedge|negedge) (\w+)\) (\w+)\) of instance \S+\s+of module (\w+)',line)
        if m and (m[1],m[2]) in ifnone.get(m[3],set()):
            key='%s %s->%s'%(m[3],m[1],m[2]);accepted[key]=accepted.get(key,0)+1
        else:bad_nep.append(line)
    net=re.findall(r'(?m)^.*\*W,SDFNET:.*$',logs)
    bad_net=[l for l in net if not (sram_ok and 'PERIOD (negedge CLK)' in l and 'RA1SHD_2048x32M8' in l)]
    ok=not fatal and not bad_nep and not bad_net
    return ok,dict(fatal=fatal[:20],unexpected_sdfnep=bad_nep[:20],unexpected_sdfnet=bad_net[:20],
                   accepted_ifnone_paths=dict(total=sum(accepted.values()),by_arc=accepted),
                   accepted_sram_negedge_period=len(net)-len(bad_net))


INPUT_PADS=('clk_i','rst_ni','tck_i','tms_i','td_i')


def accept_time0_pad_conflicts(state):
    """Treat only the input PADs' time-0 BUS CONFLICT reports as non-fatal.

    At time 0 the testbench already drives the input PADs while the tie cells
    on their OEN pins have not propagated through their SDF delay, so the IO
    model sees X on PAD once. Any other error, or a conflict at another time
    or on another PAD, keeps the run failed."""
    pattern=re.compile(r'ERROR :\s+0 \+\+BUS CONFLICT\+\+ : tb_fullchip\.dut\.i_pad_(\w+)$')
    errors=state.get('errors') or []
    hits=[e for e in errors if pattern.match(e.strip()) and pattern.match(e.strip())[1] in INPUT_PADS]
    if (state.get('status')=='failed' and state.get('exit_code')==0 and state.get('marker')
            and hits and len(hits)==len(errors) and not state.get('reason')):
        state=dict(state,status='complete',accepted_time0_input_pad_conflicts=hits)
    return state,len(hits) if state.get('status')=='complete' else 0


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--candidate',type=Path,required=True)
    args=ap.parse_args();candidate=args.candidate.resolve();out=candidate/'sdf'
    if not metrics(candidate).get('eligible'):raise RuntimeError('candidate has not passed physical acceptance')
    artifacts=json.loads((candidate/'artifact_sha256.json').read_text())
    if not artifacts or any(digest(candidate/p)!=sha for p,sha in artifacts.items()):
        raise RuntimeError('physical artifacts changed since acceptance')
    if out.exists() and not (out/'status.json').exists():
        # No tool can have started before launch writes its status. Preserve
        # partially generated inputs, then start a new, reviewable attempt.
        out.rename(candidate/('sdf_incomplete_'+str(time.time_ns())))
    libs=libraries();testbench=candidate/'driver/tb_fullchip.sv'
    if not out.exists():
        out.mkdir()
        with gzip.open(candidate/'outputs/routed.sdf.gz','rt') as source:
            text,rewritten=sram_clk_q_noedge(source.read(),Path(libs['macro_verilog']).read_text(errors='replace'))
        (out/'routed.sdf').write_text(text)
        save(out/'sdf_rewrite.json',dict(source=str(candidate/'outputs/routed.sdf.gz'),
             source_sha256=digest(candidate/'outputs/routed.sdf.gz'),sram_clk_q_iopaths_unqualified=rewritten))
        subprocess.run(['python3',str(HERE/'make_trace.py'),str(out/'commands.txt')],check=True,cwd=ROOT)
        # Clock-domain-crossing capture flops sample asynchronous inputs by
        # design; a Verilog timing check there turns Q into X. Disable checks
        # on exactly these flops (and nothing else), found in the netlist.
        netlist=(candidate/'outputs/simulation.v').read_text()
        cdc=sorted(set(re.findall(r'\\(i_host_(?:req|ack)_sync_reg\[0\]|i_host_(?:tck|sys)_reset_sync_reg\[[01]\]) ',netlist)))
        if tuple(cdc)!=CDC_CAPTURE:raise RuntimeError('unexpected CDC capture flops: '+repr(cdc))
        (out/'cdc.tfile').write_text(''.join('PATH tb_fullchip.dut.\\%s  -tcheck\n'%name for name in cdc))
        save(out/'cdc_tcheck_disabled.json',dict(instances=cdc,reason='asynchronous capture stages of the JTAG/core synchronizers'))
        sdf=out/'routed.sdf'
        (out/'sdf.cmd').write_text('SDF_FILE = "%s",\nSCOPE = tb_fullchip.dut,\n'
                                   'LOG_FILE = "sdf_annotation.log",\nMTM_CONTROL = "MAXIMUM";\n' % sdf)
        save(out/'inputs_sha256.json',{str(p):digest(p) for p in [candidate/'outputs/simulation.v',out/'routed.sdf',out/'sdf.cmd',out/'cdc.tfile',out/'commands.txt',testbench]})
        command=['xrun','-64bit','-sv','-timescale','1ns/1ps','-access','+rwc','-top','tb_fullchip',
          '-sdf_cmd_file',str(out/'sdf.cmd'),'-tfile',str(out/'cdc.tfile'),
          # TSMC models use $recrem/$setuphold with delayed signals only under NTC and
          # RECREM; without them the SDF RECREM recovery/removal limits cannot annotate.
          '+define+NTC','+define+RECREM','+neg_tchk',
          '-ignr_warnmax','SDFNEP','-ignr_warnmax','SDFNET','-sdfstats','sdf_stats.log',
          str(libs['std_verilog']),str(libs['io_verilog']),str(libs['macro_verilog']),
          str(candidate/'outputs/simulation.v'),str(testbench),
          '+SDF='+str(out/'routed.sdf'),'+TRACE='+str(out/'commands.txt'),'-l','simulation.log']
        state=launch(command,out,'PNR_OPT_JTAG_PASS',timeout=14400)
    else:
        saved=json.loads((out/'inputs_sha256.json').read_text())
        if any(digest(p)!=sha for p,sha in saved.items()):raise RuntimeError('SDF inputs changed')
        state=recover_tool(out,'PNR_OPT_JTAG_PASS')
    state,time0_conflicts=accept_time0_pad_conflicts(state)
    logs='\n'.join(p.read_text(errors='replace') for p in out.glob('*.log'))
    models='\n'.join(Path(libs[k]).read_text(errors='replace') for k in ('std_verilog','io_verilog','macro_verilog'))
    sram_ok=sram_period_ok(Path(libs['macro_verilog']).read_text(errors='replace'))
    clean,diagnostics=classify(logs,ifnone_arcs(models),sram_ok)
    annotation=out/'sdf_annotation.log'
    annotated=annotation.is_file() and bool(re.search(r'(?i)annotat',annotation.read_text(errors='replace')))
    jtag=re.search(r'PNR_OPT_JTAG_PASS commands=(\d+)',logs)
    result=dict(passed=state['status']=='complete' and clean and annotated and bool(jtag),
       jtag_commands=int(jtag[1]) if jtag else None,timing_diagnostics=not clean,diagnostics=diagnostics,
       accepted_time0_input_pad_conflicts=time0_conflicts,
       annotation_log_present=annotation.is_file(),
       sources_sha256=json.loads((out/'inputs_sha256.json').read_text()))
    save(out/'result.json',result)
    if not result['passed']:raise RuntimeError('SDF acceptance failed; retained all evidence')

if __name__=='__main__':main()
