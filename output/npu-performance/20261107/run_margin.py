"""Re-optimize the accepted routed checkpoint with a temporary setup target."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parent
repo=root/'checkout_bounds'
parent=repo/'build/bounds_retry_50mhz'
out=repo/'build/bounds_margin_50mhz'
if out.exists():raise FileExistsError(out)
out.mkdir()
script=root/'margin_from_route.tcl'
helper=root/'build_helpers.tcl'
checkpoint=parent/'route.dcp'
command=['D:/AMDDesignTools/2025.2/Vivado/bin/vivado.bat','-mode','batch','-source',str(script),
         '-log',str(out/'vivado.log'),'-journal',str(out/'vivado.jou'),'-tclargs',
         str(checkpoint),str(out),str(repo),str(helper)]
with (out/'console.log').open('w') as log:
    result=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT)
provenance=json.loads((parent/'provenance.json').read_text())
provenance['command']=command
provenance['staging']=str(out)
provenance['implementation_tuning']=dict(parent_provenance_sha256=hashlib.sha256((parent/'provenance.json').read_bytes()).hexdigest(),
    source_checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
    script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
    optimization_extra_setup_uncertainty_ns=.35,signoff_extra_setup_uncertainty_ns=0,period_ns=20)
(out/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
print((out/'summary.md').read_text() if (out/'summary.md').exists() else (out/'console.log').read_text()[-5000:],flush=True)
sys.exit(result.returncode)
