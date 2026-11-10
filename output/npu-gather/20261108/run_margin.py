"""Retune a completed route, restoring original 50 MHz constraints for signoff."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--checkout',type=Path,required=True)
p.add_argument('--parent',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
root=Path(__file__).resolve().parent
repo=a.checkout.resolve();parent=a.parent.resolve();out=a.output.resolve()
prov=json.loads((parent/'provenance.json').read_text())
for name,digest in prov['source_sha256'].items():
    assert hashlib.sha256((repo/name).read_bytes()).hexdigest()==digest,name
checkpoint=parent/'route.dcp';assert checkpoint.is_file()
out.mkdir(parents=True,exist_ok=False)
script=root/'margin_from_route.tcl';helper=root/'build_helpers.tcl'
command=['D:/AMDDesignTools/2025.2/Vivado/bin/vivado.bat','-mode','batch','-source',str(script),
         '-log',str(out/'vivado.log'),'-journal',str(out/'vivado.jou'),'-tclargs',
         str(checkpoint),str(out),str(repo),str(helper)]
with (out/'console.log').open('w') as log:
    result=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT)
prov['command']=command;prov['staging']=str(out)
prov['implementation_tuning']=dict(parent_build=str(parent),
    parent_provenance_sha256=hashlib.sha256((parent/'provenance.json').read_bytes()).hexdigest(),
    source_checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
    script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
    helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
    optimization_extra_setup_uncertainty_ns=.35,signoff_extra_setup_uncertainty_ns=0,period_ns=20)
(out/'provenance.json').write_text(json.dumps(prov,indent=2)+'\n')
print((out/'summary.md').read_text() if (out/'summary.md').exists() else (out/'console.log').read_text()[-3000:],flush=True)
raise SystemExit(result.returncode)
