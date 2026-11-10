# ABI 5.0 FPGA 验证记录

- 源码：`38fdd73e02082cbb00d9cb6e27800af837479e76`，独立 checkout。
- 归档：`D:/IWS/advanced_soc/ChipDesign/build/fpga_signoff/fpga_38fdd73_20261107_083726`。
- 配置：Vivado 2025.2，xc7a35tcsg324-1，8×8 PE，50 MHz 系统时钟，TCK 约束 1 MHz。
- CDC 动态仿真通过：普通 TAP 100 组、突发 TAP 32 组。原始日志位于 `cdc_dynamic/`。
- 只读下载口探测：Xilinx/1234-tulA 检出 xc7a35t_0，探测时 DONE=0。原始日志位于 `board_probe/`。
- FPGA 存储配置 RTL（`CHIPDESIGN_FPGA_MEM`）：85 个程序用例通过，19 个负向与生命周期检查记录见 `fpga_mem_rtl/summary.json`。
- 50 MHz Vivado 实现使用上述同一提交，运行工作目录为 `D:/temp/fpga_38fdd73_20261107_083726`，布局前优化结束后，构建因资源超限停止。
- CDC 约束门禁单元检查 3/3 通过。板测工具和证据核对器修正旧 ABI 4.0 的 4 列和 TinyCNN 非融合例外后，离线单元检查 8/8 通过。修订补丁见 `board-tools-abi5.patch`，不涉及 RTL。
- 截至 08:51（上海时间），Vivado 正在综合时序优化。构建结束后，本轮归档任务会复制实现产物；生成两份 routed CDC 输入时会运行端点复核。当前尚未下载 bitstream 或执行实板验证。

## 09:02（北京时间）最终结果

- 构建 FAILED，停在 place，最后完成阶段为 opt。布局前 DRC UTLZ-1 报告逻辑 LUT 需要 32,957 个位置，器件可用 20,800 个。优化后总 Slice LUT 为 33,901 / 20,800（162.99%）。
- 50 MHz 的布局前估算 WNS −6.898 ns、TNS −4477.917 ns、WHS +0.045 ns。布局器没有启动，未生成布线后的 setup/hold 结果。
- 综合阶段 CDC 约束审计覆盖请求 72/72 位和应答 37/37 位，20 ns 限制下最小余量分别为 +18.964 ns、+18.975 ns。普通与突发 TAP 动态仿真均已通过。未生成 route_cdc.rpt 或 route_npu_jtag_cdc.tsv，物理 CDC 签核仍待证。
- 未生成 bitstream，未启动实板验证。原始构建结果保存在 impl_50mhz/。
