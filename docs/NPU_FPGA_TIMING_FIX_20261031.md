# NPU FPGA 时序分析与修复（2026-10-31）

## 基线与问题性质

本次以 `codex/fpga-cdc-timing-review-20261030` 的报告及对应硬件为基线，
修复分支为 `codex/npu-fpga-timing-fix-20261031`。报告提交为 `346808f`，
它记录的硬件提交为 `f63a49132ec1b2131d1c59adf86ec9cfc0bd5a3a`；两者硬件相同。
当前工作原先位于 `exp/no_cpu`，其提交保留在原分支，未覆盖或回退。

**最终结果：50 MHz 常用完整构建 PASS，setup +0.064 ns、hold +0.035 ns，
setup/hold 违例端点均为 0；两种 SRAM 配置各 74 项功能回归通过，推理周期保持不变。**
机器可读摘要及源码/产物哈希见
[validation/npu_fpga_timing_fix_20261031.json](validation/npu_fpga_timing_fix_20261031.json)。

原报告的问题是 **setup，不是 hold**：

| 原报告 routed 指标 | 数值 |
|---|---:|
| core 时钟 | 50 MHz / 20 ns |
| setup WNS / TNS | −3.961 / −5382.919 ns |
| setup 违例端点 | 2534 |
| hold WHS / 违例端点 | +0.031 ns / 0 |
| CDC request / response payload 最小余量 | +18.147 / +18.359 ns |

WNS 是最差 setup 余量，TNS 是所有 setup 违例端点的负余量之和，
不是一条路径需要几千 ns。setup 失败表示数据路径过慢；hold 检查数据是否过早改变，
二者不能混淆。原报告的 CDC mailbox 路径通过，不能通过切掉这些 CDC 路径修复系统域内违例。

## 原因：同周期过多控制与地址计算串联

原报告的最差路径从 activation 状态寄存器到 activation BRAM `ENBWREN`，
共 33 级逻辑，数据延迟 23.190 ns，其中逻辑 7.209 ns、布线 15.981 ns。
本机重新综合相同 RTL，也出现同类路径：`issue_p` → activation BRAM enable，
34 级逻辑，setup WNS −3.901 ns。这是逻辑结构导致的真实同步路径问题。

源码中相应的依赖为：

```text
activation rsp_valid
  → gather_return / gather_full → issue_can_fire
  → 选择当前/下一个坐标与基地址
  → padding 边界计算和 calc_mask
  → calc_first → 16-bit offset 连续性运算
  → a_valid → activation read_miss / defer
  → 写队列 pop / write_ready / wf → BRAM enable
```

`47d7276` 引入的读 miss 优先仲裁提高了推理效率，但也使读请求在同周期影响写队列接收、
SRAM 地址选择和使能。与响应后才计算下一次 gather 的逻辑串联后，形成较长路径。
这不表示读优先协议或运算结果错误，而是原有组合逻辑边界不适合该 FPGA 的 50 MHz 目标。
`wf` 的高扇出进一步增加布线代价。

本机用相同实现脚本重新布线未修改基线，包含布局前后物理优化，仍然得到
setup WNS −0.713 ns、hold +0.033 ns。前 50 条最差 setup 路径中，
39 条从 `cursor_x` 发出，11 条从 `slot_busy` 发出，终点分布于 36 个 D、13 个 CE 和一个 BRAM enable。
这说明问题覆盖一组 gather/activation 控制路径，不能仅针对原报告的一个 BRAM 引脚处理。

网表中的 `perf_events`、`sample_q` 等名字可能属于共享组合逻辑，
不能仅凭这些名字断言性能计数器或 trace RAM 是根因，也不能把路径上的组合 net 名当成额外寄存级。

## RTL 修改

只修改 `hardware/npu/rtl/npu_program_core.sv` 的 gather 元数据计算：

1. **提前准备下一位置。** gather 启动后，当前事务的地址、mask 等已保存在 `gather_*`。
   因此在 `gather_state != 0` 时即可计算下一位置，不必等待 `issue_can_fire`。
   请求是否发射仍由原来的 `issue_fire`、`has_next` 和背压共同控制。
2. **预计算组内连续性。** 在已有 `kernel_prepare` 周期，与 `k_offsets` 一同寄存六个
   lane pair 的连续性标志。请求阶段只需检查所有被 mask 选中的 pair 是否连续。
   这与“每个有效 lane 的 offset 相对第一个有效 lane 保持相同字节距离”等价，
   包含空 mask、单 lane、padding 空洞及非连续访问。

没有新增流水周期、结果槽或完整激活寄存器阵列；RTL 只增加六个组内标志位。
保留 ABI 2.1、五算子、融合、量化位宽、双 SRAM、读 miss 优先策略、写合并/转发、
背压、flush、性能计数和 FPGA trace。

第一步单独综合时，最差路径从 34 级降到 31 级，WNS 仍为 −3.111 ns；
因此继续移出了 mask 之后的 16 位连续性运算。没有把这一中间版本当作时序通过。
两项合并后首轮路由的 setup WNS 为 −1.220 ns，经过已有的布局后物理优化和 Explore 重布线，
最终达到 setup +0.220 ns / hold +0.035 ns。这是最终选用的精简 RTL。

另外评估了提前寄存 padding/stride 有效坐标上下界的候选。它的综合结果更好，
但最终 routed setup 只有 +0.027 ns，且需要更多 LUT/FF。
考虑面积和实测余量，**没有保留该候选及其专用边界函数**。
这也说明逻辑级数和综合 WNS 不能单独决定最终方案，布线和布局结果必须参与判断。

新增 `tiny_strided_asymmetric` 功能场景：一像素、stride=2、非对称 padding，
验证空间边界 mask 和非连续 gather；与原场景一起在两种阵列和融合设置下运行。

`hardware/fpga/scripts/implement_nocpu.tcl` 增加了前 50 条 setup、前 20 条 hold、
高扇出、层次资源、CDC 和时钟交互报告，便于检查整组路径。
`build_ego1.tcl` 的完整构建入口也对齐到 checkpoint helper 已有的物理优化流程：
布局后、路由前执行 `phys_opt_design`；首轮 setup 为负时再做一次
`phys_opt_design -directive AggressiveExplore` 和 `route_design -directive Explore`。
这修复了完整构建与 checkpoint 实现入口策略不一致的问题。
时钟周期、I/O 预算和 CDC 例外均未放宽；两种入口继续执行 setup/hold 和 CDC 审计。

## 验证结果

### 功能与周期

| 检查 | 结果 |
|---|---|
| 行为 SRAM、wave trace 开启 | 74/74 场景，每场景两次推理，135284 次张量写回逐项检查 |
| 两颗真实 SRAM Verilog 宏模型、wave trace 关闭 | 同样 74/74；全部场景的完整指标与行为版一致 |
| 固定基线源码对比 | 34 个 4×8 场景的两次周期、写回、全部性能计数和逐算子 trace 完全一致 |
| 4×4、JTAG、融合/非融合、五算子、边界和非法描述符 | 完整 74 项回归覆盖并通过 |
| 随机 SRAM 压力 | 8006 次读、9329 次写，写/读停顿 10004/11713 周期，队列峰值 8；行为/宏版一致 |
| Q31 量化 | 20000 个向量通过 |
| CDC 复位、停钟、恢复 | ModelSim PASS，Errors 0 / Warnings 0 |
| FPGA 主机验证器单元测试 | 8/8，通过；其中哈希错误拒绝是预期负向测试 |

原有 70 个场景的完整性能指标不变。
训练模型维持 3262/3262 周期；原始 TinyCNN8 维持 3362/3362 周期。
本次优化没有以增加推理周期换取时序，也没有删去读优先仲裁。

固定基线来自 `git archive 346808f` 的独立源码快照；最终回归摘要的源码哈希与当前文件一致。
最初启动的 `build/timing_20261031_baseline_regression` 在工作目录继续编辑时跨版本编译，
不作为固定基线证据；采用的是 `build/timing_20261031_baseline_sources/build/regression/summary.json`。

本机直接运行 ModelSim 时遇到中文路径数据库错误，随后在 ASCII 临时目录用相同四个源码重跑通过，
来源哈希保存在 `build/timing_20261031_cdc_reset_ascii/provenance.json`。
Python 测试使用工作区内临时目录及 UTF-8 模式绕过沙箱 Temp/GBK 环境问题；未据此改动硬件。

### FPGA 实现

Vivado 2025.2，`xc7a35tcsg324-1`，core 50 MHz、TCK 1 MHz。
同一套综合流程的结果如下；这些是 **综合估计，不能代替 routed STA**：

| 指标 | 未修改基线 | 最终精简 RTL | 边界预计算候选（未保留） |
|---|---:|---:|---:|
| setup WNS | −3.901 ns | −1.427 ns | +0.408 ns |
| setup TNS | −4993.500 ns | −265.195 ns | 0 ns |
| 最差路径逻辑级数 | 34 | 25 | 21 |
| 最差路径 CARRY4 数量 | 9 | 3 | 3 |
| LUT | 16712 | 16784 | 16996 |
| FF | 10727 | 10734 | 10808 |
| BRAM tile / DSP | 8 / 48 | 8 / 48 | 8 / 48 |

最终精简综合网表 LUT 相对基线增加 72（约 0.43%）、FF 增加 7。
这里的 FPGA 资源包含启用的 trace，不是 ASIC 面积测量。

相同 checkpoint 实现流程下的完整 routed 结果：

| 指标 | 未修改基线 | 最终精简 RTL | 边界预计算候选（未保留） |
|---|---:|---:|---:|
| setup WNS | −0.713 ns | +0.220 ns | +0.027 ns |
| hold WHS | +0.033 ns | +0.035 ns | +0.033 ns |
| LUT | 16579 | 16564 | 16677 |
| FF | 10759 | 10735 | 10808 |
| BRAM tile / DSP | 8 / 48 | 8 / 48 | 8 / 48 |
| DRC errors | 0 | 0 | 0 |

### 常用完整构建入口的最终结果

从头运行 `build_nocpu.py --mhz 50 --implement`，最终 `Result: PASS`，
完成综合、P&R、时序/CDC/I/O 审计及 bitstream 生成。
实际最终结果与 checkpoint 入口单独列出，避免把后者较好的余量当作本次 bitstream 的签核结果：

| 指标 | 完整构建最终结果 |
|---|---:|
| setup WNS / TNS | +0.064 / 0 ns |
| setup 违例端点 | 0 / 38329 |
| hold WHS / THS | +0.035 / 0 ns |
| hold 违例端点 | 0 / 38220 |
| pulse width 违例端点 | 0 |
| LUT / FF | 16564 / 10735 |
| BRAM tile / DSP | 8 / 48 |
| DRC errors | 0 |
| 未约束内部端点 / 无时钟寄存器 / 多时钟寄存器 | 0 / 0 / 0 |
| request payload | 72/72，20 ns 预算，最小 slack +18.061 ns |
| response payload | 37/37，20 ns 预算，最小 slack +18.063 ns |
| JTAG I/O max/min 路径 | 6 组全部通过 |

最终最差 setup 路径为 `issue_p[1]` → activation `cache_data[7][7]/D`，
24 级逻辑，数据延迟 19.534 ns，其中逻辑 5.223 ns、布线 14.311 ns。
最差路径仍位于核心域的 gather/activation 控制区域，但已满足 20 ns 要求。
余量较小，后续 RTL、工具或约束变更仍需重跑 P&R；本次没有放宽 50 MHz 目标。

仍保留 109 条 CDC-15 bundled-data mailbox 结构警告，未隐藏或豁免。
toggle 第一级异步入口、复位同步器 CLR 使用既有的端点例外；
同步器级间 setup/hold 和 payload max-delay 继续被审计。
`check_timing` 的一项 `no_input_delay` 来自异步复位 `rst_ni`，
两项 `no_output_delay` 来自无同步接收规范的 LED 输出例外；
原有专用 I/O 审计确认 reset/LED 不存在意外的有限同步数据路径，三路 JTAG 数据 I/O 的实际路径均接受检查。
这不等于所有 methodology/DRC warning 为零，也不是 CDC 协议形式证明。

bitstream：`build/timing_20261031_release_full/fpga_top.bit`，SHA-256：

```text
1ef35851eb7c72267c252e320374cf57e38ab72a8a790b892acff1fa6cc73d33
```

没有连接或下载板卡。完整构建的源码哈希、回归源码哈希均与最终工作树逐项核对一致。

## 复现与证据

在仓库根目录执行，输出目录使用尚不存在的新目录，工具和课程 SRAM 模型路径按本机位置替换：

```powershell
python hardware/fpga/scripts/build_nocpu.py --vivado-root D:/Project/Vivado/2025.2/Vivado --output build/fpga_timing_fix_repro --jobs 4 --mhz 50 --implement
python hardware/npu/scripts/run_program_regression.py --output build/timing_fix_repro_behavior --trained-model software/models/tinycnn8_flatten_strong.npz
python hardware/npu/scripts/run_program_regression.py --macro-model build/private_ip/RA1SHD_2048x32M8.v --wave-disabled --output build/timing_fix_repro_macro --trained-model software/models/tinycnn8_flatten_strong.npz
```

本次交付的主要证据：

- 完整构建：`build/timing_20261031_release_full/summary.md`、`provenance.json`、
  `route_timing_summary.rpt`、`route_setup_paths.rpt`、`route_hold_paths.rpt`、
  `route_npu_jtag_cdc.tsv`、`route_cdc.rpt`、`route_drc.rpt`、`io_audit/`。
- 最终行为/宏模型回归：`build/timing_20261031_release_regression/summary.json`、
  `build/timing_20261031_release_macro_regression/summary.json`。
- 同流程基线和候选对照：`build/timing_20261031_baseline_route/`、
  `build/timing_20261031_final_route/`（最终精简 RTL 的 checkpoint 流程）、
  `build/timing_20261031_bounds_route/`（未保留候选）。
- CDC 复位专项：`build/timing_20261031_cdc_reset_ascii/sim.log`。

构建产物和课程 SRAM 私有模型不提交到 Git。机器可读摘要已记录源码及主要产物 SHA-256。
本次验证范围是 RTL 仿真及 FPGA 综合、P&R、STA；未重新运行 ASIC 物理签核或实板推理。
