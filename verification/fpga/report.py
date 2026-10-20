#!/usr/bin/env python3
"""Recheck evidence and emit report.json/report.md. 0=accepted scope, 1=invalid/incomplete, 2=signoff blocked."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct

ROOT=Path(__file__).resolve().parents[2]
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return Path(path).read_text(encoding='utf-8-sig')
def load(path): return json.loads(read(path))
def require(condition,message):
    if not condition: raise ValueError(message)
def signed(x): return x-(1<<32) if x>>31 else x

def verify_timing(out, audit):
    require(sha(out/'audit/timing.json')==audit['timing_json_sha256'],'Timing JSON changed')
    timing=load(out/'audit/timing.json')
    match=re.search(r'WNS\(ns\)[^\n]*\n[^\n]*\n\s*([-+\d.]+)\s+([-+\d.]+)\s+\d+\s+\d+\s+([-+\d.]+)',read(out/'audit/route_timing_summary.rpt'))
    require(match is not None,'Timing table missing')
    for key,value in (('wns_ns',match[1]),('whs_ns',match[3])):
        require(math.isfinite(timing[key]) and abs(timing[key]-float(value))<=0.0011,'Timing JSON/report mismatch: '+key)
    return timing

def verify_dmi_constraints(out, audit):
    require(sha(out/'audit/dmi_constraints.tsv')==audit['dmi_constraints_sha256'],'DMI constraint evidence changed')
    with (out/'audit/dmi_constraints.tsv').open() as f: rows=list(csv.DictReader(f,delimiter='\t'))
    require(len(rows)==6 and {r['group'] for r in rows}=={c+'/'+k for c in ('req','resp') for k in ('data','req','ack')},'DMI crossing coverage incomplete')
    for row in rows:
        bound=float(row['bound_ns']); slack=float(row['min_slack_ns'])
        require(int(row['endpoints'])==int(row['paths'])>0,'DMI paths cut or missing')
        require(math.isfinite(bound) and 0<bound<=20.001 and math.isfinite(slack) and slack>=0,'DMI effective timing invalid')

def verify_reset(out, item, expected):
    require(item['status']=='PASS' and item['jobs']==2,'Reset recovery incomplete')
    require(item['logits']==expected,'Reset golden mismatch')
    state=item['reset_state']
    require(state['0x70000008']==4 and all(state[k]==0 for k in ('0x7000000c','0x70000410','0x7000006c')),'Reset control state mismatch')
    if item['interrupted_active_workload']:
        require(item['before'][0]==0x12345678 and item['before'][3]>0,'Active reset evidence missing')
    require(Path(item['result_file']).name==item['result_file'],'Invalid reset result path')
    raw=out/'recovery'/item['result_file']
    require(sha(raw)==item['result_sha256'],'Reset raw result changed')
    w=struct.unpack('<32I',raw.read_bytes())
    require(w[0]==0xc0dec0de and w[1]==0 and w[3]==2 and w[4]==0x20002 and w[6]==0,'Reset firmware raw result failed')
    require(list(map(signed,w[8:16]))==expected and w[31]==2*(w[3]+w[28]),'Reset raw logits/IRQ mismatch')

def collect(out):
    manifest=load(out/'manifest.json'); mh=sha(out/'manifest.json')
    require(manifest['schema']==2,'Legacy evidence lacks hardened receipts; preserve it and run a new validation directory')
    for name,h in manifest['source_sha256'].items():
        require(sha(ROOT/name)==h,'Live source changed: '+name)
        require(sha(out/'source_snapshot'/name)==h,'Source snapshot changed: '+name)
    require(sha(out/'validation.bin')==manifest['firmware_sha256'],'Firmware identity mismatch')
    deployment=load(out/'deployment.json')
    identity_path=out/'program/build_identity.json'
    require(sha(identity_path)==deployment['build_identity_sha256'],'Build identity changed')
    identity=load(identity_path)
    require(identity['bitstream_sha256']==deployment['bitstream_sha256'],'Build/bitstream mismatch')
    hardware_sources={name.replace('\\','/'):h for name,h in manifest['source_sha256'].items()
        if name.replace('\\','/').startswith('hardware/') and Path(name).suffix in ('.sv','.svh','.v','.xdc','.f','.tcl')}
    require(identity['source_sha256']==hardware_sources,'Build/validation hardware input set mismatch')
    require(deployment['status']=='PASS' and deployment['manifest_sha256']==mh,'Deployment provenance mismatch')
    require(sha(deployment['bitstream'])==deployment['bitstream_sha256'],'Bitstream changed after programming')
    require(sha(out/'program/console.log')==deployment['program_log_sha256'],'Program log changed')
    program=read(out/'program/console.log')
    for marker in ('LAB6_PROGRAM_COMMAND=PASS','LAB6_FPGA_DONE=1','LAB6_FPGA_CRC_ERROR=0'):
        require(marker in program,'Missing programming check '+marker)
    require('FPGA_GDB_CHECK=PASS' in read(out/'gdb.log'),'GDB check incomplete')
    board=load(out/'board/summary.json');recovery=load(out/'recovery/summary.json')
    require(board['status']=='PASS' and recovery['status']=='PASS','Board/recovery incomplete or failed')
    for part in (board,recovery): require(part['manifest_sha256']==mh,'Results refer to another manifest')
    for part in (board,recovery):
        require(datetime.fromisoformat(part['started_utc'])>=datetime.fromisoformat(deployment['programmed_utc'].replace('Z','+00:00')),'Board evidence predates programming')
    require(board['bitstream_sha256']==deployment['bitstream_sha256'],'Board/deployment identity mismatch')
    require(len(board['cases'])==len(manifest['cases']),'Partial board result')
    total_jobs=total_checks=irq_checks=0
    for case,item in zip(manifest['cases'],board['cases']):
        require(case['name']==item['name'],'Board case order mismatch')
        for key,hkey in (('payload','sha256'),('model','model_sha256')):
            require(sha(out/case[key])==case[hkey],'Vector changed: '+case['name'])
        w=struct.unpack('<32I',(out/'board'/(case['name']+'.bin')).read_bytes())
        require(w[0]==0xc0dec0de and w[1]==0 and w[3]==case['repeats'] and w[4]==0x20002 and w[6]==0,'Firmware raw result failed: '+case['name'])
        require(list(map(signed,w[8:16]))==case['expected']==item['logits'],'Golden mismatch: '+case['name'])
        require(w[31]==2*(w[3]+w[28]),'IRQ coverage missing: '+case['name'])
        require(w[2]==item['checks'] and w[3]==item['jobs'] and w[31]==item['irq_checks'],'Raw/JSON mismatch')
        if case['mode']==3: require(w[30]==case['repeats'],'Busy-negative tests incomplete')
        total_jobs+=w[3];total_checks+=w[2];irq_checks+=w[31]
    require(sorted({c['classes'] for c in manifest['cases']})==list(range(1,9)),'Missing class-count coverage')
    expected_patterns={'zero','ones','alternating','walking_bits','address','random'}
    require({x['pattern'] for x in recovery['sram']}==expected_patterns,'Missing full-SRAM patterns')
    for item in recovery['sram']:
        rb=out/'recovery'/(item['pattern']+'_readback.bin')
        require(rb.stat().st_size==8192 and sha(rb)==item['sha256'],'SRAM raw result mismatch')
        require(rb.read_bytes()==(out/'recovery'/(item['pattern']+'.bin')).read_bytes(),'SRAM compare failed')
    resets=recovery['resets']
    require(len(resets)>=2 and any(x['interrupted_active_workload'] for x in resets),'Missing active reset coverage')
    for item in resets:
        verify_reset(out,item,next(c['expected'] for c in manifest['cases'] if c['name']=='random_c4_mode1'))
    rtl=load(out/'rtl_receipt.json');fused=load(out/'rtl_fused_summary.json')
    require(rtl['status']=='PASS' and rtl['manifest_sha256']==mh,'RTL receipt mismatch')
    require(sha(out/'rtl_fused_summary.json')==rtl['fused_summary_sha256'],'RTL summary changed')
    for name,key in (('rtl_fused.log','fused_log_sha256'),('rtl_soc.log','soc_log_sha256')):
        require(sha(out/name)==rtl[key],'RTL log changed')
    require('ALL FUSED TINYCNN8 TESTS PASSED' in read(out/'rtl_fused.log'),'NPU RTL incomplete')
    require('PASS structure regression;' in read(out/'rtl_soc.log'),'SoC RTL incomplete')
    require(len(fused['units'])==7 and len(fused['negative_event_checks'])==10 and fused['element_comparisons']>0,'RTL coverage incomplete')
    require(len(fused['cases'])>=21 and len(set(fused['units']))==7,'RTL cases/units incomplete')
    audit=load(out/'audit/receipt.json')
    require(audit['manifest_sha256']==mh and audit['bitstream_sha256']==deployment['bitstream_sha256'],'Audit provenance mismatch')
    require(sha(audit['checkpoint'])==audit['checkpoint_sha256'],'Routed checkpoint changed')
    require(audit['checkpoint_sha256']==identity['checkpoint_sha256'] and audit['build_identity_sha256']==sha(identity_path),'Checkpoint/bitstream build mismatch')
    for name,key in (('cdc_details.rpt','cdc_sha256'),('route_timing_summary.rpt','timing_sha256'),('route_methodology.rpt','methodology_sha256'),('route_drc.rpt','drc_sha256')):
        require(sha(out/'audit'/name)==audit[key],'Audit report changed')
    for name,key in (('clock_interaction.rpt','clock_interaction_sha256'),('exceptions_coverage.rpt','exceptions_sha256')):
        require(sha(out/'audit'/name)==audit[key],'Constraint audit changed')
    verify_dmi_constraints(out,audit)
    cdc={m[0]:dict(severity=m[1],count=int(m[2])) for m in re.findall(r'^(CDC-\d+)\s+(Critical|Warning|Info)\s+(\d+)\s+',read(out/'audit/cdc_details.rpt'),re.M)}
    require(bool(cdc),'CDC report not recognized')
    timing=verify_timing(out,audit);blocks=[]
    drc=read(out/'audit/route_drc.rpt')
    if re.search(r'^\|[^\n]*\|\s*(Error|Critical Warning)\s*\|',drc,re.M): blocks.append('Routed DRC error/critical warning remains')
    if timing['wns_ns']<0 or timing['whs_ns']<0: blocks.append('Routed setup/hold timing fails')
    for rule,value in cdc.items():
        if value['severity']=='Critical': blocks.append(f'{rule}: {value["count"]} critical CDC paths require closure')
    if cdc.get('CDC-2',{}).get('count',0): blocks.append('CDC-2: acknowledge synchronizers lack ASYNC_REG properties')
    methodology=read(out/'audit/route_methodology.rpt')
    if re.search(r'^\|[^\n]*Critical Warning',methodology,re.M): blocks.append('Critical methodology violations remain')
    if re.search(r'^\|\s*TIMING-18\s*\|',methodology,re.M): blocks.append('TIMING-18: external IO delay assumptions are incomplete')
    return dict(status='BLOCKED' if blocks else 'PASS',functional_status='PASS',physical_status='BLOCKED' if blocks else 'PASS',
        commit=manifest['commit'],bitstream_sha256=deployment['bitstream_sha256'],manifest_sha256=mh,
        board_cases=len(board['cases']),board_jobs=total_jobs,firmware_checks=total_checks,irq_pending_clear_checks=irq_checks,
        sram_patterns=len(recovery['sram']),reset_cycles=len(resets),active_reset_cycles=sum(x['interrupted_active_workload'] for x in resets),
        recovery_jobs=sum(x['jobs'] for x in resets),rtl_units=len(fused['units']),rtl_cases=len(fused['cases']),
        rtl_element_comparisons=fused['element_comparisons'],rtl_negative_checker_tests=len(fused['negative_event_checks']),
        timing=timing,cdc=cdc,blockers=blocks,limits=board['gaps']+[
            'IRQ pending/clear is observed through CPU mip; interrupt trap entry, ISR and mret are not exercised.',
            'Reset trials use DM ndmreset, not physical button/PLL loss or cycle-exact NPU busy timing.',
            'No post-route timing simulation, formal equivalence, exhaustive ISA compliance, voltage/temperature sweep or functional/code coverage closure.',
            'Bitstream/checkpoint association uses the selected accepted build, programming receipt and hashes; not physical configuration readback proof.'])

def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output).resolve();require(out.is_relative_to(ROOT/'build'),'Output must be inside build/')
    report=dict(status='FAIL',functional_status='UNKNOWN',physical_status='UNKNOWN')
    try: report.update(collect(out))
    except Exception as e: report['error']=str(e)
    report.update(generated_utc=datetime.now(timezone.utc).isoformat(),reporter_sha256=sha(__file__))
    report['workflow_tools_sha256']={str(p.relative_to(ROOT)):sha(p) for p in
        [ROOT/'verification/fpga'/name for name in ('run.ps1','report.py','gdb_check.gdb','audit_cdc.tcl','README.md')]+[ROOT/'scripts/lab6/program_board.tcl']}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    lines=['# FPGA verification result','',f'Overall: **{report["status"]}**; functional: **{report["functional_status"]}**; physical: **{report["physical_status"]}**.','']
    if report['functional_status']=='PASS':
        lines += [f'- Board: {report["board_cases"]} cases, {report["board_jobs"]} inference jobs, {report["firmware_checks"]} firmware checks, {report["irq_pending_clear_checks"]} IRQ checks.',
            f'- Recovery: {report["sram_patterns"]} full-SRAM patterns, {report["reset_cycles"]} resets ({report["active_reset_cycles"]} active-workload interruptions), {report["recovery_jobs"]} additional inference jobs.',
            f'- RTL: {report["rtl_units"]} unit runs, {report["rtl_cases"]} golden cases, {report["rtl_element_comparisons"]} element comparisons; SoC/DMA/SRAM structure regression PASS.',
            f'- Routed timing: WNS {report["timing"]["wns_ns"]} ns, WHS {report["timing"]["whs_ns"]} ns.',
            f'- Bitstream SHA256: `{report["bitstream_sha256"]}`.','', '## Unresolved signoff gates','']
        lines += ['- '+x for x in report['blockers']] or ['- None within the declared scope.']
        lines += ['', '## Coverage limits','']+['- '+x for x in report['limits']]
    else: lines+=['Evidence validation failed: '+report.get('error','Unknown failure')]
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
    return {'PASS':0,'FAIL':1,'BLOCKED':2}[report['status']]
if __name__=='__main__': raise SystemExit(main())
