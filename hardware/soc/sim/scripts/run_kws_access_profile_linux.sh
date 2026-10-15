#!/usr/bin/env bash
# Passive whole-SoC KWS access profiler entry point; leaves all output in build/.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../.." && pwd)
firmware=""; output=""; compile_timeout=600; test_timeout=300
config=asic
while (($#)); do
    case "$1" in
        --config) config=${2:?}; shift 2 ;;
        --firmware) firmware=${2:?}; shift 2 ;;
        --output) output=${2:?}; shift 2 ;;
        --timeout) test_timeout=${2:?}; shift 2 ;;
        --compile-timeout) compile_timeout=${2:?}; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
case "$config" in asic|fpga) ;; *) echo 'Config must be asic or fpga' >&2; exit 2 ;; esac
[[ $test_timeout =~ ^[1-9][0-9]*$ && $compile_timeout =~ ^[1-9][0-9]*$ ]] || exit 2
firmware=$(realpath -e -- "${firmware:?--firmware is required}")
output=$(realpath -m -- "${output:-$root/build/kws_sim_$(date +%Y%m%d_%H%M%S)}")
case "$firmware" in "$root/build/"*) ;; *) echo 'Firmware must be in build/' >&2; exit 2 ;; esac
case "$output" in "$root/build/"*) ;; *) echo 'Output must be in build/' >&2; exit 2 ;; esac
mkdir -p -- "$root/build"
mkdir -- "$output"
exec > >(tee "$output/regression.log") 2>&1
echo "Memory config: $config"
if ! command -v xrun >/dev/null && [[ -f /opt/eda/env.sh ]]; then
    set +u
    source /opt/eda/env.sh
    set -u
fi
command -v xrun >/dev/null
command -v timeout >/dev/null
xrun -version > "$output/simulator_version.log" 2>&1
filelist="$output/workload.f"
: > "$filelist"
if [[ $config == fpga ]]; then
    printf '+define+CHIPDESIGN_FPGA_MEM\n' >> "$filelist"
fi
while IFS= read -r line || [[ -n $line ]]; do
    line=${line%$'\r'}
    [[ -n $line ]] || continue
    case "$line" in
        hardware/soc/sim/tb/*) ;; # Select only the dedicated workload top.
        +incdir+*) printf '+incdir+%s/%s\n' "$root" "${line#+incdir+}" ;;
        +*.svh) printf '"%s/%s"\n' "$root" "${line#+}" ;;
        *) printf '"%s/%s"\n' "$root" "$line" ;;
    esac
done < "$root/hardware/soc/sim/filelists/chipdesign_soc.f" >> "$filelist"
printf '"%s/hardware/soc/sim/tb/kws_workload_tb.sv"\n' "$root" >> "$filelist"
printf '"%s/hardware/soc/sim/tb/kws_access_profile_tb.sv"\n' "$root" >> "$filelist"
active_pid=""
cleanup() {
    if [[ -n $active_pid ]]; then
        kill -TERM "$active_pid" 2>/dev/null || true
        wait "$active_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
bounded() {
    local seconds=$1 log=$2 status=0
    shift 2
    timeout --signal=TERM --kill-after=5s "${seconds}s" "$@" > "$log" 2>&1 &
    active_pid=$!
    wait "$active_pid" || status=$?
    active_pid=""
    return "$status"
}
cd -- "$output"
status=0
bounded "$compile_timeout" compile.log xrun -64bit -sv -timescale 1ns/1ps \
    -f "$filelist" -top kws_access_profile_tb -access +r -elaborate -l xrun_compile.log || status=$?
if ((status)) || grep -Eq '\*(E|F|SE),' compile.log; then
    tail -n 30 compile.log; echo "FAIL: compile exit=$status"; exit 1
fi
bounded "$test_timeout" simulation.log xrun -64bit -R "+firmware=$firmware" -l xrun_simulation.log || status=$?
printf 'ACCESS_IMAGE sha256=%s\n' "$(sha256sum -- "$firmware" | cut -d ' ' -f1)" >> simulation.log
if ((status)) || grep -Eq 'FAIL:|TIMEOUT|\*(E|F|SE),|^[[:space:]]*(Fatal:|Error:)' simulation.log || \
    ! grep -Eq '^KWS_PASS count=[0-9]+$' simulation.log; then
    tail -n 30 simulation.log; echo "FAIL: simulation exit=$status"; exit 1
fi
grep '^KWS_' simulation.log
grep -q '^ACCESS_AXI ' simulation.log
grep -q '^ACCESS_ID ' simulation.log
echo "PASS KWS access profile; logs: $output"
