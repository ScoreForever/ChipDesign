#!/usr/bin/env bash
# Run as root after building OpenOCD and extracting the verified xPack archive.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then echo 'Run with wsl -d Ubuntu -u root' >&2; exit 1; fi
user=${1:-enovo}
user_home=$(getent passwd "$user" | cut -d: -f6)
[[ -d "$user_home" ]] || { echo 'WSL user not found' >&2; exit 1; }
toolchain=/opt/xpack-riscv-none-elf-gcc-13.2.0-2
[[ -x "$toolchain/bin/riscv-none-elf-gdb" ]] || { echo 'xPack toolchain missing' >&2; exit 1; }
cd "$user_home/riscv-openocd"
make install > /tmp/lab6-openocd-install.log 2>&1
cat > /etc/profile.d/lab6-riscv.sh <<'PROFILE'
# Lab 6 RISC-V toolchain (xPack 13.2.0-2).
export RISCV=/opt/xpack-riscv-none-elf-gcc-13.2.0-2
case ":$PATH:" in
    *":$RISCV/bin:"*) ;;
    *) export PATH="$RISCV/bin:$PATH" ;;
esac
PROFILE
chmod 644 /etc/profile.d/lab6-riscv.sh
# Bash and zsh interactive shells do not necessarily read /etc/profile.
for rc in "$user_home/.bashrc" "$user_home/.zshrc"; do
    if [[ -f "$rc" ]] && ! grep -Fq '/etc/profile.d/lab6-riscv.sh' "$rc"; then
        cp -p -- "$rc" "$rc.lab6-backup-$(date +%Y%m%d_%H%M%S)"
        printf '\n# Lab 6 RISC-V tools\n[ ! -f /etc/profile.d/lab6-riscv.sh ] || . /etc/profile.d/lab6-riscv.sh\n' >> "$rc"
    fi
done
cat > /etc/udev/rules.d/60-lab6-ft232h.rules <<'RULE'
# External course JTAG adapter only; allow plugdev members to use libusb.
SUBSYSTEM=="usb", ATTR{idVendor}=="0403", ATTR{idProduct}=="6014", MODE="0660", GROUP="plugdev", TAG+="uaccess"
RULE
chmod 644 /etc/udev/rules.d/60-lab6-ft232h.rules
if ! udevadm control --reload-rules 2>/dev/null; then
    # This WSL distro uses Microsoft's init instead of systemd.
    /usr/lib/systemd/systemd-udevd --daemon
    udevadm control --reload-rules
fi
source /etc/profile.d/lab6-riscv.sh
openocd --version
riscv-none-elf-gdb --version | head -n 2
