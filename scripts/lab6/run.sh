#!/usr/bin/env bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
action=${1:-check}
target=${2:-nocpu}
if [[ "$target" != nocpu ]]; then
    echo 'Target must be nocpu' >&2
    exit 2
fi
case "$action" in
    check)
        command -v openocd
        openocd --version
        openocd -f "$repo/software/host/lab6_ft232h.cfg" -c 'echo LAB6_OPENOCD_CHECK=PASS; shutdown'
        command -v lsusb
        if ! lsusb; then echo 'No USB devices are currently attached to WSL.'; fi
        echo 'LAB6_ALL_TOOLS_CHECK=PASS'
        ;;
    openocd)
        exec openocd -f "$repo/software/host/lab6_ft232h.cfg" -c 'set NPU_JTAG_KHZ 10' -f "$repo/software/host/nocpu_openocd.cfg"
        ;;
    *) echo 'Usage: run.sh {check|openocd} [nocpu]' >&2; exit 2 ;;
esac
