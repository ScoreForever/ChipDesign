#!/usr/bin/env python3
"""Keep the real tool exit receipt even if its experiment controller disconnects."""
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

command=json.loads(sys.argv[1]);receipt=Path(sys.argv[2])
try:
    child=subprocess.Popen(command)
    def terminate(signum,frame):
        child.terminate()
        try:child.wait(timeout=20)
        except subprocess.TimeoutExpired:child.kill();child.wait()
    signal.signal(signal.SIGTERM,terminate)
    code=child.wait()
    error=None
except Exception as exc:
    code=127;error=str(exc)
value=dict(exit_code=code,finished=time.time(),error=error,
           command_sha256=hashlib.sha256(json.dumps(command).encode()).hexdigest())
tmp=receipt.with_suffix('.tmp');tmp.write_text(json.dumps(value)+'\n');tmp.replace(receipt)
sys.exit(code if 0<=code<256 else 1)
