"""Normalize a clean OpenOCD checkout copied/cloned with Windows CRLF."""
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"]).strip():
    raise SystemExit("Refusing to normalize a checkout with local changes")
subprocess.run(["git", "-C", str(root), "config", "--local", "core.autocrlf", "false"], check=True)
paths = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"]).split(b"\0")
count = 0
for name in paths:
    if not name:
        continue
    path = root / name.decode()
    if not path.is_file() or path.is_symlink():
        continue
    data = path.read_bytes()
    if b"\0" not in data and b"\r\n" in data:
        path.write_bytes(data.replace(b"\r\n", b"\n"))
        count += 1
print(f"Normalized {count} text files to LF")
subprocess.run(["git", "-C", str(root), "diff", "--exit-code"], check=True)
