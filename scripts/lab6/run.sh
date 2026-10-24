#!/usr/bin/env bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ -f /etc/profile.d/lab6-riscv.sh ]]; then
    source /etc/profile.d/lab6-riscv.sh
fi
action=${1:-check}
target=${2:-cpu}
case "$action" in
    check)
        command -v openocd
        openocd --version
        # Check driver and target availability without creating/examining a
        # hardware target. RISC-V target cleanup itself can access the DTM.
        openocd -f "$repo/software/host/lab6_ft232h.cfg" -c 'if {[lsearch -exact [target types] riscv] < 0} {error "RISC-V target missing"}; echo LAB6_OPENOCD_CHECK=PASS; shutdown'
        command -v riscv-none-elf-gdb
        riscv-none-elf-gdb -q -nx -batch -ex 'set architecture riscv:rv32' -ex 'show architecture'
        riscv-none-elf-gcc --version | head -n 1
        command -v lsusb
        if ! lsusb; then echo 'No USB devices are currently attached to WSL.'; fi
        echo 'LAB6_ALL_TOOLS_CHECK=PASS'
        ;;
    openocd)
        cfg="$repo/software/host/lab6_ft232h.cfg"
        case "$target" in
            nocpu)
                [[ -f "$repo/software/host/nocpu_openocd.cfg" ]] || { echo 'The current branch does not contain the CPU-less target config.' >&2; exit 2; }
                exec openocd -f "$cfg" -c 'set NPU_JTAG_KHZ 10' -f "$repo/software/host/nocpu_openocd.cfg"
                ;;
            cpu)
                exec openocd -f "$cfg" -f "$repo/software/host/lab6_cv32e40p.cfg" -c init -c halt
                ;;
            *) echo 'Target must be nocpu or cpu' >&2; exit 2 ;;
        esac
        ;;
    gdb)
        if [[ "$target" != cpu ]]; then
            echo 'GDB requires the CV32E40P bitstream; select target cpu.' >&2
            exit 2
        fi
        exec riscv-none-elf-gdb -q -nx -ex 'set architecture riscv:rv32' -ex 'target remote localhost:3333'
        ;;
    *) echo 'Usage: run.sh {check|openocd|gdb} [nocpu|cpu]' >&2; exit 2 ;;
esac
