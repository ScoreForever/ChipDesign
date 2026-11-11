"""Verify frozen inputs/exports and prototype gates; retain raw signoff limits."""
from pathlib import Path
import os,json,hashlib
R=Path(os.environ['EDA_RUN'])
C=Path((R/'selected_candidate.path').read_text().strip())
def j(p): return json.loads(p.read_text())
def verify(base,values):
    for name,sha in values.items():
        if hashlib.sha256((base/name).read_bytes()).hexdigest()!=sha: raise RuntimeError('hash mismatch: '+name)
verify(R/'src',j(R/'eda/sources.json'))
verify(C,j(C/'artifact_sha256.json'))
assert j(C/'result.json')['eligible']
assert j(R/'analysis/expanded_drv_audit.json')['passed']
cases=('sdf','sdf_cnn_f1','sdf_cnn_f0')
for case in cases:
    result=j(C/case/'result.json')
    assert result['passed'],case
    verify(Path('/'),result['sources_sha256'])
    if case!='sdf': assert result['trace_counts_match'],case
independent={name:j(C/'signoff'/name/'result.json') for name in ('drc','lvs','sta','lec')}
assert all(v['passed'] for v in independent.values())
print(json.dumps({'frozen_sources_and_artifacts_verified':True,'sdf_passes':len(cases),'prototype_policy_checks_passed':True,'raw_drc_results':independent['drc']['total'],'raw_lvs_correct':independent['lvs']['correct'],'scope':'TT prototype validation. Local IO deviations and SRAM STA black boxes are not foundry signoff.'},indent=2))
