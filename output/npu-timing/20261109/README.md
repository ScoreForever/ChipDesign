# 50 MHz RTL 时序余量恢复验收（2026-11-09）

已完成 RTL 优化、完整实现、同构建 CDC 复核与 FPGA 上板验证。当前本地 RTL 与已烧录镜像的冻结输入逐项 SHA-256 相同。ABI 5.0、8×8 阵列、原 50 MHz 时钟约束保持不变。本目录保存验收时的原始报告与镜像。

## 实测结论

| 项目 | 本轮起点（forward） | 本轮选定版本（peek） |
|---|---:|---:|
| Routed WNS | +0.031 ns | **+0.533 ns** |
| TNS | 0 | 0 |
| WHS | +0.034 ns | +0.028 ns |
| LUT | 18,464（88.77%） | 18,425（88.58%） |
| FF | 7,263 | 7,466 |
| DSP | 12 | 20 |
| BRAM tiles | 4 | 4 |

WNS 比本轮起点改善 0.502 ns，比要求恢复的历史 +0.344 ns 高 0.189 ns。优化增加 203 个 FF、8 个 DSP，LUT 减少 39。资源仍较紧，最终路径已转为激活 BRAM 输出到结果槽，见 `vivado_peek/route_setup_paths.rpt`；本结果不代表更高频率或 ASIC 签核。

## RTL 修改及周期保持方式

在 `hardware/npu/rtl/npu_program_core.sv` 增加提前一个位置的影子游标，以及下一次激活读请求的地址、掩码、首有效行和有效标志寄存器。将地址步进、边界判断、行选择从当前请求发起路径提前到既有准备周期/前一个成功 issue 周期。影子状态只在真实 issue 时前进，阻塞时保持；第一笔请求在既有 K-group 准备结束时预装，不增加准备拍或请求间隔。

新增仿真断言核对初始位置为 p0，以及影子游标始终领先当前 issue 一个位置。改动范围见 `selected_core.patch`（相对本轮开始快照），另有 `selected_tb.patch`。早期 spec 并行组合试验及 bound 参数末索引缓存未纳入最终版本。

## 性能与功能

| 模型 | 优化前周期 | 新镜像板上两次周期 |
|---|---:|---:|
| ECG | 15,854 | 15,854 / 15,854 |
| Fashion CNN | 47,176 | 47,176 / 47,176 |
| GTSRB L | 95,147 | 95,147 / 95,147 |
| HAR | 18,836 | 18,836 / 18,836 |
| KWS L | 86,536 | 86,536 / 86,536 |
| KWS M | 88,754 | 88,754 / 88,754 |
| MNIST L | 37,059 | 37,059 / 37,059 |

七个模型各使用第一个 golden frame、连续两次运行，14 次输出正确、周期不回退；全部 336 个板上性能计数值（14×24）与同源 RTL 生产重放一致。此处不是完整数据集精度评估。详见 `performance_comparison.csv`、`board_counter_comparison.json` 和 `board_final/board_peek_final/report.json`。

选定源码的验证包括：81 个程序及 19 组负向/生命周期回归、15 组 gather/pooling 边界、8 组传输、18 组 native 路径，以及 7 模型关闭 trace 的生产重放。81 个程序的周期和 24 个计数器逐例与起点一致。对应 `peek_*json` 保留原始记录。

## CDC 与上板顺序

先检查同源构建的最终时序与 CDC，再运行 `gate_before_program.py`，最后烧录及上板。最终时序要求 WNS≥+0.344 ns、TNS=0、WHS≥0，源码/生产回归/实现 provenance 与 bitstream 哈希一致后才放行。未修改时钟约束或增加 CDC 豁免。

- 动态实际 TAP：100+32 项回归通过。
- 静态 CDC：109 个 bundled-data 端点（72 请求、37 响应）逐项复核；无非预期条目，无新增豁免，原始警告保留。
- FPGA：显式选择配置线缆 1234-tulA，DONE=1、CRC_ERROR=0。
- FT232H：210512180081，TCK=1 MHz；五项板上检查通过：忙时暂停 TCK 1 秒、重复 START、协议 RESET、watchdog 超时恢复、恢复后重新推理。
- 测试结束已协议 RESET，busy=false、无错误；自有 OpenOCD 已关闭。

物理复位按键、电气波形检查仍需人工完成，未计入本次通过范围。

## 可复核交付物

- `vivado_peek/fpga_top.bit`：已烧录镜像。
- `vivado_peek/summary.md`、`route_timing_summary.rpt`、`route_setup_paths.rpt`、`route_hold_paths.rpt`、`route_utilization.rpt`：最终布线报告。
- `vivado_peek/provenance.json`、`peek_sources_sha256.json`、`program_gate.json`：源码与镜像一致性门禁。
- `cdc_peek/review.json`：同构建 CDC 审核与动态日志。
- `board_final/board_cdc_peek_final/report.json`：五项板上 CDC/恢复检查。
- `program_peek.log`、`vivado_peek/programmed_board_properties.txt`：编程状态。
- `implementation_flow.json`：一次 route_design，无负余量 AggressiveExplore 重试。

Bitstream SHA-256：`9859c10155bb19638164b7b50b46b1751dd19821a00744fa8634c494d8df0c7e`。

远端隔离构建：`D:/IWS/advanced_soc/ChipDesign/build/fpga_signoff/npu_timing_20261109/checkout_peek/build/timing_peek_50mhz`。本轮保留原有未提交改动及侧聊的主机工具优化，没有覆盖其文件。
