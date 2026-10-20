#!/usr/bin/env bash
set -euo pipefail

EXPS=/home/s2400012724/chip-fullstack-2026/ChipDesign_postsim_exps
FLOW=$EXPS/pnr_no_cpu_areaopt_20261020
RTL=$EXPS/rtl/ChipDesign-exp-no_cpu-20261020
HANDOFF=$FLOW/pnr/my_soc_top/outputs/manual_final
OUT=$FLOW/build/postlayout_sim
MACRO=$FLOW/src/macro/RA1SHD_2048x32M8
mkdir -p "$OUT"
cd "$OUT"

sed -e 's/clk_i_pad/clk_i/g' \
    -e 's/rst_ni_pad/rst_ni/g' \
    -e 's/tck_i_pad/tck_i/g' \
    -e 's/tms_i_pad/tms_i/g' \
    -e 's/td_i_pad/td_i/g' \
    -e 's/td_o_pad/td_o/g' \
    -e 's/irq_o_pad/irq_o/g' \
    "$HANDOFF/my_soc_top_hier_postpnr.v" > my_soc_top_hier_postpnr_sim.v
gzip -dc "$HANDOFF/my_soc_top_tt_io.sdf.gz" > my_soc_top_tt_io.sdf

DATA=$OUT/data
python3 - "$DATA" "$RTL" <<'PY'
import sys
sys.path.insert(0, sys.argv[2] + '/hardware/npu/tools')
from tinycnn8_golden import generate
generate(sys.argv[1], seed=7, classes=4, pattern='random')
PY

source /opt/eda/env.sh
xrun -64bit -sv -timescale 1ns/1ps -access +rwc \
    -top tb_nocpu_postlayout \
    /lec/pdk/tsmc180/tcb018gbwp7t_290a/TSMCHOME/digital/Front_End/verilog/tcb018gbwp7t_270a/tcb018gbwp7t.v \
    /lec/pdk/tsmc180/tpd018nv_280a/TSMCHOME/digital/Front_End/verilog/tpd018nv_260a/tpd018nv.v \
    "$MACRO/RA1SHD_2048x32M8.v" \
    my_soc_top_hier_postpnr_sim.v \
    "$FLOW/src/sim/tb_nocpu_postlayout.sv" \
    "+DATA_DIR=$DATA" \
    -l xrun_postlayout_sdf.log
