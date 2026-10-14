#!/usr/bin/env bash
# Run the same seven tests as run_structure_regression.ps1 with Xcelium or VCS.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../.." && pwd)
test_timeout=120
compile_timeout=600
sim=xcelium
output=""
while (($#)); do
    case "$1" in
        --sim) sim=${2:?}; shift 2 ;;
        --timeout) test_timeout=${2:?}; shift 2 ;;
        --compile-timeout) compile_timeout=${2:?}; shift 2 ;;
        --output) output=${2:?}; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
case "$sim" in
    xcelium) simulator=xrun ;;
    vcs) simulator=vcs ;;
    *) echo 'Simulator must be xcelium or vcs' >&2; exit 2 ;;
esac
[[ $test_timeout =~ ^[1-9][0-9]*$ && $compile_timeout =~ ^[1-9][0-9]*$ ]] || {
    echo 'Timeouts must be positive integer seconds' >&2; exit 2;
}
output=${output:-"$root/build/vcs_structure_$(date +%Y%m%d_%H%M%S)"}
mkdir -p -- "$root/build"
output=$(realpath -m -- "$output")
case "$output" in "$root/build/"*) ;; *) echo 'Output must be inside the worktree build directory' >&2; exit 2 ;; esac
mkdir -- "$output"
exec > >(tee "$output/regression.log") 2>&1
echo "Logs: $output"
echo "Simulator: $sim"
if ! command -v "$simulator" >/dev/null && [[ -f /opt/eda/env.sh ]]; then
    # Some EDA environment scripts assume unset shell variables are permitted.
    set +u
    source /opt/eda/env.sh
    set -u
fi
command -v "$simulator" >/dev/null || { echo "FAIL: $simulator is not on PATH"; exit 1; }
command -v timeout >/dev/null || { echo 'FAIL: GNU timeout is required'; exit 1; }
if [[ $sim == vcs ]]; then
    vcs -ID > "$output/simulator_version.log" 2>&1
else
    xrun -version > "$output/simulator_version.log" 2>&1
fi

# Convert ModelSim +header entries to source files; keep order and include paths.
filelist="$output/chipdesign_soc_linux.f"
while IFS= read -r line || [[ -n $line ]]; do
    line=${line%$'\r'}
    [[ -n $line ]] || continue
    case "$line" in
        +incdir+*) printf '+incdir+%s/%s\n' "$root" "${line#+incdir+}" ;;
        +*.svh) printf '"%s/%s"\n' "$root" "${line#+}" ;;
        *) printf '"%s/%s"\n' "$root" "$line" ;;
    esac
done < "$root/hardware/soc/sim/filelists/chipdesign_soc.f" > "$filelist"
for tb in hardware/npu/tb/tb_requant_unit.sv hardware/npu/tb/tb_tinycnn8_capacity.sv hardware/soc/sim/tb/tb_axi_sram_pipeline.sv; do
    printf '"%s/%s"\n' "$root" "$tb" >> "$filelist"
done

# GNU timeout owns the compiler/simulator process group, including children.
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
bad_compile_log() {
    # Compiler warnings echo source code, including $fatal/$error statements.
    # Only structured diagnostics and error-count summaries indicate failure.
    if [[ $sim == vcs ]]; then
        grep -Eq '^[[:space:]]*(Error|Fatal)-\[' "$1"
    else
        awk '/^[[:space:]]*([[:alnum:]_]+(\([^)]*\))?:[[:space:]]*)?\*(E|F|SE),/ { bad=1 }
             { line=tolower($0)
               if (line ~ /^[[:space:]]*errors:[[:space:]]*0*[1-9][0-9]*([[:space:],]|$)/ ||
                   line ~ /^[[:space:]]*0*[1-9][0-9]*[[:space:]]+errors?([[:space:].,]|$)/) bad=1 }
             END { exit !bad }' "$1"
    fi
}
bad_simulation_log() {
    awk '/^[[:space:]]*([[:alnum:]_]+(\([^)]*\))?:[[:space:]]*)?\*(E|F|SE),/ { bad=1 }
         /^[[:space:]]*(Fatal:|Error:|Fatal-\[|Error-\[)/ { bad=1 }
         # Do not interpret warning text or echoed SV statements as TB output.
         /\*W,|\$(fatal|error|display)[[:space:]]*\(/ { next }
         /(^|[^[:alnum:]_])(FAIL:|TIMEOUT)([^[:alnum:]_]|$)/ { bad=1 }
         END { exit !bad }' "$1"
}
run_test() {
    local top=$1 name=$2 marker=$3 clock=$4
    shift 4
    local dir="$output/$name" status=0
    local parameter
    local -a overrides=()
    for parameter in "$@"; do
        if [[ $sim == vcs ]]; then
            overrides+=("-pvalue+$top.$parameter")
        else
            overrides+=(-defparam "$top.$parameter")
        fi
    done
    mkdir -p -- "$dir/runtime/hardware/soc/sim/out"
    # Preserve repository-relative hex paths while keeping all waveforms in build/.
    ln -s -- "$root/hardware/soc/sim/sw" "$dir/runtime/hardware/soc/sim/sw"
    ln -s -- "$root/hardware/soc/sim/tb" "$dir/runtime/hardware/soc/sim/tb"
    cat > "$dir/metrics.sv" <<EOF
\`timescale 1ns/1ps
module regression_metrics;
  longint unsigned clock_edges = 0;
  always @(posedge $top.$clock) clock_edges++;
  final \$display("METRICS $name simulation_time_ns=%0.3f clock_edges=%0d", \$realtime, clock_edges);
endmodule
EOF
    echo "Running $name ($top)"
    # Each test has its own snapshot/library. Xcelium builds and runs from the
    # same runtime root so -R selects this test's snapshot and hex paths work.
    pushd "$dir/runtime" >/dev/null
    if [[ $sim == vcs ]]; then
        bounded "$compile_timeout" "$dir/compile.log" vcs -full64 -sverilog -debug_access+all \
            -timescale=1ns/1ps -f "$filelist" "$dir/metrics.sv" \
            -top "$top" -top regression_metrics "${overrides[@]}" -o "$dir/simv" || status=$?
    else
        bounded "$compile_timeout" "$dir/compile.log" xrun -64bit -sv -timescale 1ns/1ps \
            -f "$filelist" "$dir/metrics.sv" -top "$top" -top regression_metrics \
            -access +r -elaborate "${overrides[@]}" -l "$dir/xrun_compile.log" || status=$?
    fi
    popd >/dev/null
    if ((status)) || bad_compile_log "$dir/compile.log"; then
        echo "FAIL: $name compile exit=$status; log: $dir/compile.log"
        tail -n 25 "$dir/compile.log"; return 1
    fi
    pushd "$dir/runtime" >/dev/null
    if [[ $sim == vcs ]]; then
        bounded "$test_timeout" "$dir/simulation.log" "$dir/simv" || status=$?
    else
        bounded "$test_timeout" "$dir/simulation.log" xrun -64bit -R -l "$dir/xrun_simulation.log" || status=$?
    fi
    popd >/dev/null
    if ((status)) || bad_simulation_log "$dir/simulation.log" || ! grep -Fq "$marker" "$dir/simulation.log" || \
        ! grep -Fq "METRICS $name simulation_time_ns=" "$dir/simulation.log"; then
        echo "FAIL: $name simulation exit=$status (124/137 indicates wall timeout); log: $dir/simulation.log"
        tail -n 25 "$dir/simulation.log"; return 1
    fi
    grep -E 'PASS|METRICS|Time:' "$dir/simulation.log"
}
run_test tb_requant_unit requant 'ALL REQUANT UNIT TESTS PASSED' clk
run_test tb_tinycnn8_capacity capacity_asymmetric 'ALL FLAT CAPACITY TESTS PASSED' clk
run_test tb_tinycnn8_capacity capacity_generic 'ALL FLAT CAPACITY TESTS PASSED' clk \
    ACT_BANK_BYTES=4096 ACT_A_BANK_BYTES=4096
run_test chipdesign_soc_tb soc 'PASS: integrated SoC test OK' clk_i
run_test chipdesign_dma_tb dma 'PASS: NPU DMA test OK' clk_i
run_test tb_axi_sram_pipeline sram_pipeline 'ALL AXI SRAM PIPELINE TESTS PASSED' clk
run_test tb_axi_sram_pipeline sram_legacy 'ALL AXI SRAM PIPELINE TESTS PASSED' clk \
    READ_LATENCY=1
echo "PASS structure regression; logs: $output"
