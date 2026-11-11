#!/bin/bash
# Licensed course Linux environment; freeze the tested commit in a fresh run.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
RUN=${1:?Usage: reproduce.sh /absolute/path/to/new-run}
case "$RUN" in /*) ;; *) echo 'Use an absolute run path' >&2; exit 2;; esac
if [ -e "$RUN" ]; then echo 'Preserve existing runs; choose a new directory' >&2; exit 2; fi
mkdir -p "$RUN/src"
git -C "$REPO" archive aa77887 | tar -x -C "$RUN/src"
source /opt/eda/env.sh
source /lec/eda.env
set -uo pipefail
# Put Icarus 12+ on PATH after the EDA environment if RTL regression is needed.
if [ -n "${IVERILOG_BIN:-}" ]; then export PATH="$IVERILOG_BIN:$PATH"; fi
export EDA_RUN="$RUN"
cd "$RUN/src"
mkdir -p build
python3 -m unittest discover -s hardware/asic/optimize -p 'test_*.py' > "$RUN/flow_tests.log" 2>&1
python3 -m unittest discover -s software/tests -p 'test_npu_*.py' > "$RUN/software_tests.log" 2>&1
python3 -m unittest discover -s software/tests -p 'test_nocpu_*.py' > "$RUN/host_tests.log" 2>&1
python3 -m unittest discover -s hardware/npu/scripts -p 'test_*.py' > "$RUN/tooling_tests.log" 2>&1
python3 -m unittest discover -s hardware/npu/tools -p 'test_*.py' > "$RUN/golden_tests.log" 2>&1
python3 hardware/npu/scripts/run_program_regression.py --packaged-models --jobs 4 --output "$RUN/src/build/rtl_full" > "$RUN/rtl_full.log" 2>&1
MACRO=$(python3 "$HERE/get_macro.py")
python3 hardware/npu/scripts/run_program_regression.py --packaged-models --wave-disabled --macro-model "$MACRO" --jobs 4 --output "$RUN/src/build/rtl_macro_waveoff" > "$RUN/rtl_macro_waveoff.log" 2>&1
python3 hardware/asic/optimize/flow.py synth --clock-gating --cols 8 --output "$RUN/eda" > "$RUN/synth_controller.log" 2>&1
python3 hardware/asic/optimize/flow.py trial --clock-gating --cols 8 --name latest_diag75 --utilization .75 --layout diagonal_facing --halo 26 --hold-target .12 --output "$RUN/eda" > "$RUN/pnr_controller.log" 2>&1
python3 "$HERE/continue_drv.py" > "$RUN/continue_drv_controller.log" 2>&1
python3 "$HERE/continue_drv_target.py" > "$RUN/target_controller.log" 2>&1
CAND="$RUN/eda/candidates/latest_diag75_drvfix2"
printf '%s\n' "$CAND" > "$RUN/selected_candidate.path"
mkdir -p "$RUN/analysis"
python3 "$HERE/audit_expanded_drv.py" > "$RUN/expanded-drv-audit.log" 2>&1
python3 hardware/asic/optimize/postlayout.py --candidate "$CAND" > "$RUN/sdf_controller.log" 2>&1
python3 "$HERE/sdf_extra.py" > "$RUN/extra_sdf_controller.log" 2>&1
python3 hardware/asic/optimize/signoff.py --candidate "$CAND" > "$RUN/signoff_controller.log" 2>&1
python3 "$HERE/verify_results.py" > "$RUN/delivery_verification.json"
cd "$CAND"
xvfb-run -a -s '-screen 0 1600x1200x24' innovus -files "$HERE/render_views.tcl" > "$RUN/render-final-view.log" 2>&1
python3 "$HERE/analyze_synthesis.py" > "$RUN/analysis-synthesis.log" 2>&1
python3 "$HERE/analyze_physical.py" > "$RUN/analysis-physical.log" 2>&1
python3 "$HERE/audit_sdf_results.py" > "$RUN/sdf-audit.log" 2>&1
python3 "$HERE/snapshot_static.py" > "$RUN/snapshot-static.log" 2>&1
echo 'Inspect every result.json, raw DRC/LVS report, and timing summary; tool exit alone is insufficient.'
