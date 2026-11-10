"""Reject a mismatched checkout before starting the isolated Vivado build."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

root=Path(__file__).resolve().parent
repo=Path(sys.argv[1]).resolve()
expected=json.loads((root/'steps_sources_sha256.json').read_text())
for name,digest in expected.items():
    assert hashlib.sha256((repo/name).read_bytes()).hexdigest()==digest,name
print(f'PASS: {len(expected)} frozen build inputs verified',flush=True)
raise SystemExit(subprocess.call([sys.executable,str(repo/'hardware/fpga/scripts/build_nocpu.py'),
    '--vivado-root','D:/AMDDesignTools/2025.2/Vivado','--output',sys.argv[2],
    '--mhz','50','--cols','8','--implement','--jobs','4'],cwd=repo))
