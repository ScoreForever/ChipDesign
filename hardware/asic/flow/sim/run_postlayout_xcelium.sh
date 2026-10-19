#!/usr/bin/env bash
set -euo pipefail

# Required locations on the Linux EDA server.
: "${CHIPDESIGN_ROOT:?set CHIPDESIGN_ROOT to the RTL repository copy}"
: "${CHIPDESIGN_PNR_ROOT:?set CHIPDESIGN_PNR_ROOT to the PNR flow directory}"

out_dir="${1:-${CHIPDESIGN_ROOT}/build/postlayout_sim}"
handoff="${CHIPDESIGN_PNR_ROOT}/pnr/my_soc_top/outputs/manual_final"
mkdir -p "${out_dir}"

# Innovus keeps module-port and external PAD-net names distinct in its database,
# but this release's hierarchical saveNetlist output omits the alias connection.
# Normalize only those six external net identifiers for simulation.
sed -e 's/clk_i_pad/clk_i/g' \
    -e 's/rst_ni_pad/rst_ni/g' \
    -e 's/tck_i_pad/tck_i/g' \
    -e 's/tms_i_pad/tms_i/g' \
    -e 's/td_i_pad/td_i/g' \
    -e 's/td_o_pad/td_o/g' \
    "${handoff}/my_soc_top_hier_postpnr.v" \
    > "${out_dir}/my_soc_top_hier_postpnr_sim.v"
gzip -dc "${handoff}/my_soc_top_tt_io.sdf.gz" \
    > "${out_dir}/my_soc_top_tt_io.sdf"

source /opt/eda/env.sh
cd "${out_dir}"
xrun -64bit -sv -timescale 1ns/1ps -access +rwc \
    -top chipdesign_soc_postlayout_tb \
    /lec/pdk/tsmc180/tcb018gbwp7t_290a/TSMCHOME/digital/Front_End/verilog/tcb018gbwp7t_270a/tcb018gbwp7t.v \
    /lec/pdk/tsmc180/tpd018nv_280a/TSMCHOME/digital/Front_End/verilog/tpd018nv_260a/tpd018nv.v \
    "${CHIPDESIGN_PNR_ROOT}/src/macro/RA1SHD_2048x32M8/RA1SHD_2048x32M8.v" \
    my_soc_top_hier_postpnr_sim.v \
    "${CHIPDESIGN_ROOT}/hardware/soc/sim/tb/chipdesign_soc_postlayout_tb.sv" \
    "+firmware=${CHIPDESIGN_ROOT}/hardware/soc/sim/sw/chipdesign_npu_test.hex" \
    -l xrun_postlayout_sdf.log
