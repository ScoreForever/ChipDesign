from pathlib import Path
import os,signal
p=Path('/proc/11/cmdline')
if p.exists() and b'/npu_gather_20261108/keep_wsl.py' in p.read_bytes():
    os.kill(11,signal.SIGTERM)
    print('Stopped owned WSL keepalive')
else:
    print('Owned WSL keepalive already exited; no process changed')
