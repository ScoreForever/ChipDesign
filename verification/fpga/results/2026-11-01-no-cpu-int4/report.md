# no_cpu_INT4 FPGA 50 MHz 实板验证报告

> 最新实板追加：[忙时物理按键复位功能采样与断电后重下载恢复](../2026-11-02-physical-followup/report.md)通过；电气波形因无测量仪器仍未实测。

> 2026-11-02 追加验证：[实际 TAP 动态与结构复核](../2026-11-02-cdc-review/report.md)、[实板停 TCK、忙时协议复位和 RST/S6 物理复位验证](../2026-11-02-cdc-board/report.md)已完成并通过。本文及原始 suite JSON 保留 11 月 1 日的历史范围，后续覆盖状态以追加报告为准。

**结论：50 MHz 时序验收通过，EGo1 FPGA 下载和 ABI 3.0 INT4 实板自动功能回归通过。**

验证对象是 `no_cpu_INT4` 的 ABI 3.0 CPU-less NPU，目标器件 `xc7a35tcsg324-1`。50 MHz 全实现、修改后的 RTL 回归、FPGA 配置、板上 JTAG 推理和独立证据复核分别留有证据。自动 suite 的结果是 `passed=true`；证据复核器返回“自动证据已核实，仍有人工项待执行”。这不是完整板级签核：忙时暂停 TCK、物理按键复位、断电重配及电气测试尚未执行。

## 构建、RTL 与镜像身份

| 项目 | 值 |
|---|---|
| 起始分支/提交 | `no_cpu_INT4` / `02185d8cb04542181facf264deb93b8eee2fc4e4` |
| FPGA | EGo1，`xc7a35tcsg324-1` |
| 时钟约束 | FPGA 输入 100 MHz，SoC 时钟 50 MHz（周期 20.000 ns），NPU TCK 1 MHz |
| Vivado | 2025.2，build 6299465 |
| 构建结果 | `PASS`; routed timing 全部约束满足；DRC 0 error |
| Bitstream SHA-256 | `BCEC1C7476F1D2423144D9E25C7D188B484C4CA1DCEDDB71B18305B00F3DCDCA` |
| 构建 provenance SHA-256 | `D8B89A71BBBA8CF50AA6E118B6AA702BC8A45C1D13B089C86BBC5C38603704C9` |

镜像来自 `build/int4_fpga_impl_20261101_timing_r1/fpga_top.bit`。构建 provenance 记录了本次 RTL 与 XDC 的源文件哈希；实板 suite 对照 provenance 时 `source_mismatches=[]`。bitstream 文件本身保留在本地 ignored `build/` 目录，报告分支保存 SHA、provenance 和完整文本证据。

## RTL 时序修复

基线 50 MHz 布线结果的 setup WNS 为 −0.277 ns、TNS 为 −7.127 ns，79 个 setup endpoint 违例；WHS 为 +0.020 ns。最差路径从 `scan_last_x_reg[3]` 出发，经空间扫描边界、gather mask 和 activation store，终止于 `q_data_reg[7][4]/D`。总数据延迟 20.055 ns，其中逻辑 4.587 ns、布线 15.468 ns；24 级逻辑。布线占路径延迟 77.1%。

修复在 `npu_program_core.sv` 增加 `cursor_at_last_x` 状态位：初始化扫描时预置，并在 issue 推进游标时同步更新；地址/边界计算使用该状态位，移除 `cursor_x == scan_last_x` 比较器对每个 gather 请求的直接扇出。扫描坐标、边界及地址行为由原有融合/非融合、pooling、stride、padding 和容量测试共同覆盖。

修复后路由 setup WNS 为 **+0.075 ns**、TNS **0.000 ns**、setup failing endpoints **0**；hold WHS **+0.034 ns**、THS **0.000 ns**、hold failing endpoints **0**。Vivado 报告 `All user specified timing constraints are met.` 新的最差 setup 路径为 `issue_p_reg[2]/C → activation q_data_reg[1][29]/CE`，周期要求 20.000 ns，数据延迟 19.504 ns（逻辑 5.533 ns、布线 13.971 ns，27 级逻辑）。布局布线后仍仅有 75 ps setup 裕量，虽通过约束，余量较小；后续若改布局、器件速度级或时钟约束，应重新实现并复核。

这次从未修复基线到修复版，setup WNS 改善 0.352 ns，TNS 归零、79 个负 slack endpoint 全部关闭。历史报告中曾记录 setup WNS −3.961 ns；当前结果相对该历史数字改善 4.036 ns。 :codex-annotation{index="1"}

### 资源与 DRC

| 指标 | 使用量 | 可用量 | 占用 |
|---|---:|---:|---:|
| Slice LUTs | 17,255 | 20,800 | 82.96% |
| Slice Registers | 10,347 | 41,600 | 24.87% |
| Block RAM Tile | 8 | 50 | 16.00% |
| DSP | 14 | 90 | 15.56% |

配置/BRAM guard（CFGBVS-1、REQP-1839、RBOR-1）通过。Route DRC 有 33 项 DSP 输入/输出 pipeline 建议类 warning（DPIP/DPOP），**0 个 DRC error**；它们不阻塞当前构建时序门槛。完整 DRC 与 methodology 报告见 `evidence/validated_50mhz/`。

## CDC 结构与功能证据

### 静态结构及时序

Route CDC 报告列出 CDC-3 Info 2 项、CDC-9 Info 3 项，以及 CDC-15 Warning 109 项。109 项与 JTAG request/response bundled-data payload 一一对应：请求 72 bit、响应 37 bit。Vivado 将这些 CE-controlled payload 结构报告为 warning；项目 CDC 审计逐 bit/逐 pin 覆盖了这 109 条并施加 max-delay datapath-only 约束，不是将整组跨时钟路径 false-path 掉。

| 跨域对象 | 覆盖路径 | 约束 | 最小报告 slack |
|---|---:|---:|---:|
| TCK → 50 MHz request payload | 72/72 | 20 ns bundled max delay | 18.310 ns |
| 50 MHz → TCK response payload | 37/37 | 20 ns bundled max delay | 18.535 ns |
| request toggle synchronizer second stage | 1 max + 1 min | max 20 ns / min 0 ns | 19.214 / 0.131 ns |
| response toggle synchronizer second stage | 1 max + 1 min | max 1000 ns / min 0 ns | 999.272 / 0.122 ns |

审计中的 `async_entry_D_only` 路径为 0；异步复位只作用于同步器 CLR 端。完整逐路径表在 `route_npu_jtag_cdc.tsv`，结构分类在 `route_cdc.rpt`，时钟关系见 `route_clock_interaction.rpt`。

### 实板 CDC 事务功能

FPGA 由 Vivado Hardware Manager 经线缆 `Xilinx/1234-tulA` 下载，器件为 `xc7a35t_0`、IDCODE `0362D093`。下载后 `DONE=1`、CRC error `0`。NPU fabric TAP 使用 FT232H（USB VID/PID `0403:6014`，序列号 `210512180081`），OpenOCD 识别 TAP IDCODE `0x00000001`，TCK 设为 1 MHz。注意：FPGA 配置 JTAG 和 FT232H NPU JTAG 是两条不同链路。

ABI 3.0 实板 suite 对每个模型通过 TCK 域发送命令与 payload，在 50 MHz 系统域执行，再经 response mailbox 取回结果。正向清单 40 项：39 项实板 PASS（每项两组输入/输出）、`tinycnn_plain` 按预期由主机因容量超限拒绝，没有错误地提交给硬件；负向/恢复清单 14/14 PASS。正向覆盖融合与非融合、Conv/FC/Pool/ReLU/Requant、i32、长 K、最大 K、奇数 packed ReLU、非对称 stride/padding、全 8 KiB activation、上界地址、slot pressure、重复运行及错误后的合法恢复。实板输出逐字节对软件参考值比较，所有已执行硬件输出匹配。suite `report.json` 保存每次输入/expected/actual 的哈希及原始二进制。

板测结束后执行协议 RESET 并读取状态确认 `busy=false`、`faulted=false`、`error=0`。RESET 是协议级恢复，不等同于物理复位按钮或断电重配。

专用 `verification/fpga/tb_cdc_reset.sv`（pending transaction、TCK 停止、两侧 reset、TCK 恢复后 fresh transfer）**本轮未取得仿真 PASS**。Questa `vlib.exe` 因 `Illegal System DLL Relocation` 无法启动；Icarus 在 `cdc_2phase` 的 type parameter 语法处不支持；Vivado `xvlog` 能编译且 XSim 能展开，但 XSim 生成模型阶段报 `Failed to compile generated C file xsim_1.c`。这些是验证器运行失败，不可记为 bench 通过，也不能据此推断 RTL 功能失败。对应 XSim compile/elaboration logs 位于 `evidence/cdc_dynamic_sim/`。

### 证据复核和未执行范围

独立 evidence checker 最初没有包含新加入 suite 的 `max_k_int4`、`odd_packed_relu`、`tiny_strided_asymmetric`，因此对正确的 40 项报告给出 stale-list 的 unexpected-case。已在本分支更新 checker 必测清单及其测试；4 个 checker 单元测试通过。更新后的 checker 对实板报告输出 `AUTOMATED EVIDENCE VERIFIED; MANUAL CHECKS INCOMPLETE`（预期退出码 2），核对了用例、hash 和逐字节结果。

CDC 板级功能路径已由真实 JTAG 请求/响应回归覆盖；以下专门压力/物理测试仍为 **NOT RUN**，需 RTL/板卡同学接续并填入观测值：

- 确认 busy 状态后重复 START；watchdog timeout 与恢复；busy 状态 RESET。
- 确认 busy 且 transaction pending 后暂停 TCK，再恢复 TCK。
- FPGA 物理 reset button；断电并重新配置。
- 电气、温度/电压角落及长时间运行。当前 route CDC 警告需要按 bundled-data handshake 一并复核，不能仅凭自动用例清零。

因此本报告通过的是 **50 MHz constrained implementation + 当前自动实板功能范围**；它不声明物理按键/断电、所有电气环境或 metastability MTBF 签核完成。

## 回归结果

- RTL：`ALL 80 PROGRAM TESTS PASSED`；覆盖 4×4/4×8、融合/非融合、INT4 最大 K/奇数 packed ReLU、错误 descriptor、生命周期与背压等；另有 20,000 个量化向量、双 SRAM 语义、trace、随机读写 stall/drain 检查通过。
- FPGA：Vivado full implementation `PASS`；setup/hold 均满足约束，0 setup/hold failing endpoints，0 DRC error。
- 上板：编程 `PASS`，DONE=1 / CRC=0；ABI 3.0 INT4 实板 suite `passed=true`；正向输出匹配，14 个负向/恢复用例全部通过。
- 独立证据审查：自动证据 verified；7 项手工/电气范围保留为 incomplete。

## 证据文件

- [Vivado summary](evidence/validated_50mhz/summary.md)
- [Build provenance 与源码哈希](evidence/validated_50mhz/provenance.json)
- [Route timing summary](evidence/validated_50mhz/route_timing_summary.rpt)
- [Route setup paths（50 条）](evidence/validated_50mhz/route_setup_paths.rpt) / [hold paths（20 条）](evidence/validated_50mhz/route_hold_paths.rpt)
- [CDC 分类报告](evidence/validated_50mhz/route_cdc.rpt) / [逐路径 CDC 审计](evidence/validated_50mhz/route_npu_jtag_cdc.tsv)
- [DRC](evidence/validated_50mhz/route_drc.rpt) / [clock interaction](evidence/validated_50mhz/route_clock_interaction.rpt) / [utilization](evidence/validated_50mhz/route_utilization.rpt)
- [Hardware Manager 下载后器件属性](evidence/validated_50mhz/programmed_board_properties.txt)
- [完整实板 suite 报告与各项输入/输出/包](evidence/board_suite/)
- [OpenOCD TAP/清理日志](evidence/board_suite/openocd_board.log)
- [CDC 停止时钟 bench 的模拟器日志（本轮未运行成功）](evidence/cdc_dynamic_sim/)
- [RTL regression 汇总与仿真日志](evidence/rtl_regression/summary.json)
- [修改前 50 MHz baseline timing evidence](evidence/baseline_50mhz/)
