import os
"""Summarize actual SDF inputs and diagnostics; never infer a simulation pass."""
from pathlib import Path
import json,re,gzip,collections,hashlib
R=Path(os.environ['EDA_RUN'])
C=Path((R/'selected_candidate.path').read_text().strip())
out=R/'analysis';out.mkdir(exist_ok=True)
all_results={}
for name in ('sdf','sdf_cnn_f1','sdf_cnn_f0'):
    P=C/name
    result=json.loads((P/'result.json').read_text()) if (P/'result.json').is_file() else None
    state=json.loads((P/'status.json').read_text()) if (P/'status.json').is_file() else None
    logs='\n'.join(p.read_text(errors='replace') for p in P.glob('*.log')) if P.is_dir() else ''
    all_results[name]={'result':result,'tool_state':state,
        'timing_violation_lines':len(re.findall(r'(?im)^.*timing(?: check)? violation.*$',logs)),
        'glitch_suppression_messages':len(re.findall(r'Glitch suppression',logs)),
        'warning_codes':dict(collections.Counter(re.findall(r'\*W,(\w+)',logs)))}
P=C/'sdf'
if (P/'routed.sdf').is_file():
    text=(P/'routed.sdf').read_text()
    checks=collections.Counter(re.findall(r'\((SETUPHOLD|SETUP|HOLD|RECREM|RECOVERY|REMOVAL|WIDTH|PERIOD|SKEW|NOCHANGE)\s',text))
    annotation=(P/'sdf_annotation.log').read_text(errors='replace') if (P/'sdf_annotation.log').is_file() else ''
    warns=[line for line in annotation.splitlines() if '*W,SDFNEP:' in line]
    paths=len(re.findall(r'\(IOPATH\s',text));unique_warns=len(set(warns))
    all_results['annotation']={
        'source_iopath_entries':paths,'source_interconnect_entries':len(re.findall(r'\(INTERCONNECT\s',text)),
        'source_timing_checks_by_type':dict(checks),
        'unique_sdfnep_warning_lines_in_annotation_log':unique_warns,
        'source_iopath_entries_without_sdfnep_percent':100*(paths-unique_warns)/paths if paths else None,
        'coverage_definition':'SDF source IOPATH entries minus unique SDFNEP annotation-log entries; not model path coverage or multi-corner coverage.',
        'sdfnet_warnings':[line for line in annotation.splitlines() if '*W,SDFNET:' in line],
        'disabled_cdc_checks':json.loads((P/'cdc_tcheck_disabled.json').read_text()) if (P/'cdc_tcheck_disabled.json').is_file() else None,
        'rewrite':json.loads((P/'sdf_rewrite.json').read_text()) if (P/'sdf_rewrite.json').is_file() else None}
    simulator_stats={}
    simlog=(P/'simulation.log').read_text(errors='replace')
    for kind in ('Pathdelays','Tchecks'):
        m=re.search(r'No\. of '+kind+r'\s*=\s*(\d+)\s+No\. of Disabled '+kind+r'\s*=\s*(\d+)\s+Annotated\s*=\s*([\d.]+)%\s*\((\d+)/(\d+)\)',simlog)
        if not m: raise RuntimeError('Missing simulator annotation totals for '+kind)
        simulator_stats[kind]=dict(zip(('total','disabled','percent','annotated','eligible'),map(float,m.groups())))
    all_results['annotation']['simulator_statistics']=simulator_stats
    main_state=all_results['sdf']['tool_state']
    if main_state and any('notimingchecks' in str(v).lower() for v in main_state.get('command',[])):
        raise RuntimeError('Global timing checks were disabled; do not report timing acceptance')
(out/'sdf_audit.json').write_text(json.dumps(all_results,indent=2)+'\n')
independent={name:json.loads((C/'signoff'/name/'result.json').read_text()) if (C/'signoff'/name/'result.json').is_file() else None for name in ('sta','lec','drc','lvs')}
(out/'independent_checks_all.json').write_text(json.dumps(independent,indent=2)+'\n')
print(json.dumps({'sdf_passes':{k:v['result'].get('passed') if v['result'] else None for k,v in all_results.items() if k!='annotation'},'independent':{k:v.get('passed') if v else None for k,v in independent.items()}},indent=2))
