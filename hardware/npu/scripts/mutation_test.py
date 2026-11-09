#!/usr/bin/env python3
"""Mutation testing: measure whether the verification suite detects injected RTL bugs.

Each mutant is one small, plausible design error in critical control logic.
It is applied to a private copy of hardware/ and software/ under
build/mutants/<run>/<id>/ (the working checkout is never modified), then a
representative regression subset runs in the memory configuration that
exercises that logic. A mutant is KILLED when any check fails (output
mismatch, block testbench, protocol checker $fatal, or timeout) and SURVIVED
when everything passes. A survivor is either a verification gap (add a test or
assertion) or an equivalent mutant (same behaviour by construction; justify it).

  mutation_test.py --run NAME [--only m01,m02] [--jobs 2] [--timeout 1500]
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
CORE = 'hardware/npu/rtl/npu_program_core.sv'
STORE = 'hardware/npu/rtl/npu_activation_store.sv'
SHARED = 'hardware/npu/rtl/npu_model_store.sv'
RA1SHD = ['--macro-model', 'hardware/npu/tb/ra1shd_functional_model.sv']
FPGA = ['--define', 'CHIPDESIGN_FPGA_MEM']
SUBSET = ('cnn,mlp,strided,edges,long_k,dwconv,dw_rect_pool,conv1d,rect_kernels,rowpad_kernels,'
          'residual_add,slot_pressure,u4_chain,pool_general,tiny_padded')

# id, file, original text (must occur once), mutated text, config, what the bug models
MUTANTS = [
    ('m01', CORE, '{1\'b0,l_out}+{1\'b0,lq_count}<LQ;', '{1\'b0,l_out}+{1\'b0,lq_count}<=LQ;', FPGA,
     'A2 credit check off by one: a 4th weight read can be outstanding (queue overflow)'),
    ('m02', CORE, 'wire lq_push=load_rsp && !(array_weight_we && !load_zero && !dw_hold && lq_count==0);',
     'wire lq_push=load_rsp;', FPGA, 'A2 response bypassed to the array is also queued (duplicate row)'),
    ('m03', CORE, 'wire [COLS*4-1:0] queued_row=lq_count!=0 ? lq_data[lq_head] : response_row;',
     'wire [COLS*4-1:0] queued_row=response_row;', FPGA, 'A2 queued rows ignored: array takes the live bus'),
    ('m04', CORE, 'if(lq_pop) lq_head<=lq_head==LQ-1 ? 2\'d0 : lq_head+1\'b1;',
     'if(lq_pop) lq_head<=lq_head+1\'b1;', FPGA, 'A2 queue head does not wrap at depth 3'),
    ('m05', CORE, 'req_row<=dwconv ? ROWS : req_row+1\'b1; req_addr<=req_addr+row_bytes;',
     'req_row<=dwconv ? ROWS : req_row+1\'b1; req_addr<=req_addr+4;', [], 'A2 next-row address ignores narrow 2-byte rows'),
    ('m06', CORE, 'if(array_weight_we && dwconv && load_row==0) load_data<=queued_row;',
     'if(array_weight_we && dwconv && load_row==1) load_data<=queued_row;', [], 'A2 depthwise hold latched one row late'),
    ('m07', CORE, 'wire dw_hold=dwconv && load_row!=0;', 'wire dw_hold=1\'b0;', [],
     'A2 depthwise rows wait for reads that are never issued (deadlock)'),
    ('m08', CORE, '!load_more && l_out==0 && lq_count==0) :', '!load_more) :', FPGA,
     'A2 drain condition ignores the weight queue (equivalent if bank loads always finish first)'),
    ('m09', STORE, 'assign side_ready=SIDE_PORT && !rst && !(sram_en && main_bank==side_bank);',
     'assign side_ready=SIDE_PORT && !rst;', RA1SHD, 'A side port may hit the macro the main port is using'),
    ('m10', SHARED, "wire [31:0] side_word=side_halfword ? {16'd0,side_rsp_data[31:16]} : side_rsp_data;",
     "wire [31:0] side_word=side_halfword ? {16'd0,side_rsp_data[15:0]} : side_rsp_data;", [],
     'A side-port halfword (narrow weight row) takes the wrong half'),
    ('m11', SHARED, 'if(side_fire)side_halfword<=model_read_addr[1];', 'if(side_fire)side_halfword<=1\'b0;', [],
     'A side-port halfword flag never set'),
    ('m12', STORE, 'wire drain=!rst && !memory_locked && flushable && !defer && !side_claim &&',
     'wire drain=!rst && !memory_locked && flushable && !defer &&', RA1SHD,
     'A write drain ignores a waiting side read (side starvation / livelock)'),
    ('m13', STORE, ".A(main_here ? sram_addr[10:0] : side_word[10:0]),",
     ".A(side_fire && side_bank==bank ? side_word[10:0] : sram_addr[10:0]),", RA1SHD,
     'A RA1SHD address mux gives the side port priority'),
    ('m14', STORE, 'assign match0[fs]=(age<count) && q_addr[fs]==read_word0;',
     'assign match0[fs]=(age<=count) && q_addr[fs]==read_word0;', [], 'write-queue forwarding matches a retired entry'),
    ('m15', SHARED, 'else if(store_idle)model_dirty<=0;', 'else model_dirty<=0;', [],
     'A model image dirty flag clears before queued image writes land'),
]


def apply(dst, file, old, new):
    p = dst / file
    s = p.read_text()
    if s.count(old) != 1:
        raise SystemExit(f'mutant anchor not unique in {file}: {old[:60]!r} ({s.count(old)})')
    p.write_text(s.replace(old, new))


def classify(log_text):
    if 'CHK ' in log_text:
        m = re.search(r'CHK [^\n]*', log_text)
        return 'protocol_checker', m.group(0)[:120]
    for key, pat in (('timeout', r'timeout|TimeoutExpired'), ('output_mismatch', r'got=|mismatch|expected'),
                     ('unit_bench', r'FATAL|fatal|Error|error')):
        m = re.search(pat, log_text)
        if m:
            line = log_text[max(0, log_text.rfind('\n', 0, m.start()) + 1):].split('\n', 1)[0]
            return key, line[:120]
    return 'failed', ''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True)
    ap.add_argument('--only')
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--timeout', type=int, default=1500)
    args = ap.parse_args()
    base = ROOT / 'build/mutants' / args.run
    base.mkdir(parents=True, exist_ok=True)
    # Snapshot sources once, so edits to the checkout during a long run cannot leak into later mutants.
    pristine = base / '_pristine'
    if pristine.exists():
        shutil.rmtree(pristine)
    for d in ('hardware', 'software'):
        shutil.copytree(ROOT / d, pristine / d, ignore=shutil.ignore_patterns('__pycache__', 'build'))
    results = []
    for mid, file, old, new, config, note in MUTANTS:
        if args.only and mid not in args.only.split(','):
            continue
        dst = base / mid
        if dst.exists():
            shutil.rmtree(dst)
        for d in ('hardware', 'software'):
            shutil.copytree(pristine / d, dst / d)
        apply(dst, file, old, new)
        out = dst / 'build/out'
        cmd = [sys.executable, 'hardware/npu/scripts/run_program_regression.py', '--quick', '--only', SUBSET,
               '--jobs', str(args.jobs), *config, '--output', str(out)]
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=dst, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=args.timeout)
            text, code = p.stdout, p.returncode
        except subprocess.TimeoutExpired as e:
            text, code = (e.stdout or b'').decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or ''), 'timeout'
            text += '\nTimeoutExpired'
        (dst / 'regression.log').write_text(text)
        killed = code != 0
        how, detail = classify(text) if killed else ('', '')
        row = dict(id=mid, file=file, config=' '.join(config) or 'behavioral', note=note,
                   status='KILLED' if killed else 'SURVIVED', killed_by=how, detail=detail,
                   seconds=round(time.time() - t0))
        results.append(row)
        print(json.dumps(row), flush=True)
        shutil.rmtree(dst / 'hardware'); shutil.rmtree(dst / 'software')   # keep only logs
    shutil.rmtree(pristine)
    (base / 'summary.json').write_text(json.dumps(results, indent=1) + '\n')
    k = sum(r['status'] == 'KILLED' for r in results)
    print(f'MUTATION SCORE {k}/{len(results)} killed')


if __name__ == '__main__':
    main()
