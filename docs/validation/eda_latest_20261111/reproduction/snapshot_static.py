import os
from pathlib import Path
import json,gzip,re,hashlib
R=Path(os.environ['EDA_RUN'])
C=Path((R/'selected_candidate.path').read_text().strip())
def j(p): return json.loads(p.read_text())
def txt(p): return p.read_text(errors='replace') if p.is_file() else None
d={'selected_candidate':str(C),'result':j(C/'result.json'),'metrics':j(C/'metrics.json'),
   'checks':j(C/'checks.json'),'area_breakdown':j(R/'analysis/physical_area_breakdown.json'),
   'independent':{k:j(C/'signoff'/k/'result.json') for k in ('sta','lec','drc','lvs')},
   'artifact_hashes':j(C/'artifact_sha256.json'),'synthesis_area':j(R/'eda/synthesis/area.json'),
   'synthesis_cells':j(R/'analysis/synthesis_cells.json'),'expanded_drv':j(R/'analysis/expanded_drv_audit.json')}
for key,name in {'power_report':'power.rpt','congestion_report':'congestion.rpt','placement_report':'placement.rpt','connectivity_report':'connectivity.rpt','pg_connectivity_report':'pg_connectivity.rpt','clock_trees_report':'clock_trees.rpt','clock_skew_groups_report':'clock_skew_groups.rpt','geometry':'database_geometry.tsv','worst_setup_path':'timing_setup.rpt'}.items(): d[key]=txt(C/name)
for key,name in {'clock_gating_report':'clock_gating.rpt','synthesis_timing':'timing.rpt','synthesis_area_report':'area.rpt'}.items(): d[key]=txt(R/'eda/synthesis'/name)
for key,folder in (('setup_summary','timing_setup'),('hold_summary','timing_hold')):
    d[key]=gzip.open(next((C/folder).glob('*.summary.gz')),'rt').read()
d['source_hashes_unchanged']=all(hashlib.sha256((R/'src'/p).read_bytes()).hexdigest()==sha for p,sha in j(R/'eda/sources.json').items())
if not d['source_hashes_unchanged']: raise RuntimeError('frozen source changed')
for name,sha in d['artifact_hashes'].items():
    if hashlib.sha256((C/name).read_bytes()).hexdigest()!=sha: raise RuntimeError('physical artifact changed: '+name)
d['verified_artifact_count']=len(d['artifact_hashes'])
(R/'analysis/static_results.json').write_text(json.dumps(d,indent=2)+'\n')
print(json.dumps({'source_hashes_unchanged':d['source_hashes_unchanged'],'verified_artifact_count':d['verified_artifact_count'],'independent':{k:v['passed'] for k,v in d['independent'].items()}}))
