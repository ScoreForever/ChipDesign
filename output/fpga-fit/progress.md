基线 38fdd73：33,901 / 20,800 Slice LUT，UTLZ-1 阻塞布局；最差路径 d_reg[4][2] → kernel_offset_reg[15]。候选保留 8×8、128 描述符、ABI 5.0，允许增加验证与每组启动周期。
候选 RTL 完成。85 项程序回归与 132 组 TAP CDC 动态场景通过。Yosys 估算 16,934 LUT。Vivado 正在远端构建。
BRAM 推断版 Vivado 两次内部崩溃。单端口 LUTRAM 版本复跑 85/85；估算 16,961 LUT，正在跑完整实现。保留层级诊断第一次用 read_verilog -define 被 Vivado 拒绝，已改为 fileset 属性并重试。
独立 npu_tensor_lookup 版完成 Vivado 综合（18,551 / 20,800 LUT）与布局，首轮布线 WNS −2.608 / WHS +0.043 ns。后布线优化继续。模型应答流水版完成综合（18,486 LUT）和布局（WNS −0.897 / WHS −0.044 ns），正在布线。移除 FIFO ready 组合反馈版通过 85/85，远端综合继续。
本轮 mywin 归档 D:/IWS/advanced_soc/ChipDesign/build/fpga_signoff/fpga_fit_serial_20261107_090631。
当前远端活跃 Vivado 暂存目录分别为 chipdesign_nocpu_ffa2lvau、chipdesign_nocpu__vcz4v5z、chipdesign_nocpu_liz5r758（用户 Temp 目录）。正式产物在归档下 checkout、checkout_pipeline、checkout_credit 各自的 build/。每个候选先核对 summary.md、route_timing_summary.rpt、route_cdc.rpt、route_npu_jtag_cdc.tsv，再决定是否下板。
最终候选通过 50 MHz 布线后时序与 CDC 复核，下载 EGo1 后通过 INT4 推理 3/3、板级 CDC 5/5。详见归档 README.md。
