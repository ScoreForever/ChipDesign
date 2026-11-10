#!/usr/bin/env bash
set -eu
run_root="$HOME/chip-fullstack-2026/ChipDesign_abi5_area129_icg_20261110_r2"
test ! -f "$run_root/source/README.md"
tar -xf "$run_root/source.tar" -C "$run_root/source"
tar -xf "$run_root/area129_r2_overlay.tar" -C "$run_root/source"
sed 's/ChipDesign_abi5_area129_icg_20261110/ChipDesign_abi5_area129_icg_20261110_r2/g' "$HOME/chip-fullstack-2026/ChipDesign_abi5_area129_icg_20261110/env.sh" > "$run_root/env.sh"
source "$run_root/env.sh"
cd "$CHIPDESIGN_ROOT"
python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path(os.environ['CHIPDESIGN_EDA_ROOT']);s=json.loads((r/'candidate_r2_sources_sha256.json').read_text())
for p,h in s['files'].items():assert hashlib.sha256((r/'source'/p).read_bytes()).hexdigest()==h,p
print('R2 source overlay hashes PASS')
PY
python3 hardware/asic/optimize/flow.py adopt --clock-gating --cols 8 --import-dir "$HOME/chip-fullstack-2026/ChipDesign_abi5_area129_icg_20261110/work/area129_icg/synthesis" --output "$run_root/work/area129_icg" > "$run_root/logs/adopt.log" 2>&1
nohup python3 hardware/asic/optimize/flow.py trial --clock-gating --cols 8 --name area129_icg_diag75 --utilization .75 --layout diagonal_facing --halo 26 --hold-target .12 --output "$run_root/work/area129_icg" > "$run_root/logs/pnr_controller.log" 2>&1 < /dev/null &
echo "R2_PNR_CONTROLLER_PID=$!"
