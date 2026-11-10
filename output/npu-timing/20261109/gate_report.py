"""Require routed timing acceptance and the requested setup margin."""
import argparse,re
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('summary',type=Path)
p.add_argument('--min-wns',type=float,default=.344)
a=p.parse_args()
s=a.summary.read_text()
m=re.search(r'WNS: ([\d.-]+) ns; TNS: ([\d.-]+) ns; WHS: ([\d.-]+) ns',s)
assert m and 'Result: PASS' in s,'implemented timing acceptance missing'
wns,tns,whs=map(float,m.groups())
print(f'WNS={wns:+.3f}ns (target>={a.min_wns:+.3f}); WHS={whs:+.3f}ns; TNS={tns:.3f}ns',flush=True)
assert wns>=a.min_wns and whs>=0 and tns==0,'requested routed margin not achieved'
