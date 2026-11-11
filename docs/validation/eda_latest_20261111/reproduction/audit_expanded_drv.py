from pathlib import Path
import gzip,json,re,os
R=Path(os.environ['EDA_RUN'])
C=Path((R/'selected_candidate.path').read_text().strip())
reports={}
for name in ('timing_setup',):
    p=next((C/name).glob('*.summary.gz'))
    text=gzip.open(p,'rt').read()
    rows={}
    for key in ('max_cap','max_tran','max_fanout','max_length'):
        m=re.search(r'\|\s*'+key+r'\s*\|\s*(\d+)\s*\((\d+)\)\s*\|\s*([-\d.]+)\s*\|\s*(\d+)\s*\((\d+)\)',text)
        if not m: raise RuntimeError('unrecognized expanded DRV row: '+str(p)+' '+key)
        rows[key]=dict(zip(('real_nets','real_terms','worst_violation','total_nets','total_terms'),map(float,m.groups())))
    reports[name]=rows
clean=all(all(v==0 for v in row.values()) for report in reports.values() for row in report.values())
result={'passed':clean,'reports':reports,'scope':'Innovus expanded-view SI timing summaries, separate from report_constraint'}
(R/'analysis/expanded_drv_audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
if not clean: raise RuntimeError('Expanded-view DRV violations remain')
