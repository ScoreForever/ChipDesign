# ChipDesign ASIC 综合、布局布线与后仿报告

日期：2026-10-16  
设计版本：`9d25b0965ba4c190c4ec352d4f4b9b5b5920273b`  
顶层：`my_soc_top`  
工艺：课程服务器 TSMC 180 nm、6 层金属  
结论等级：**后布局功能验证通过，但尚未达到流片 signoff 条件**

> 后续 foundry Calibre 检查已经执行。最终结果为 DRC `2424` 个结果、antenna `0`、LVS `INCORRECT`，因此 tapeout 判定为 **NO-GO**。详见 `docs/ASIC_SIGNOFF_STATUS.md`；本报告继续作为综合、P&R、STA 与 SDF 后仿记录。

## 1. 结论摘要

本次已经实际跑通以下链路：

`RTL 回归 → Genus 映射综合 → Innovus floorplan/IO/电源规划/placement/CTS/route → RC 提取 → SDF/SPEF/DEF/GDS/网表导出 → Xcelium 最大延迟 SDF 后仿`

关键结果如下：

| 项目 | 结果 |
|---|---:|
| RTL 结构回归 | 7/7 通过 |
| 映射后标准单元数 | 94,601 |
| 映射后标准单元面积 | 3,382,204.658 µm² |
| SRAM 宏 | `RA1SHD_2048x32M8`，1 个 |
| 时钟约束 | 20 ns，50 MHz |
| post-route setup WNS/TNS | +3.625 ns / 0 ns |
| post-route hold WNS/TNS | +0.023 ns / 0 ns |
| STA 分析路径 | 约 116,000 条 setup、116,000 条 hold |
| Innovus 几何 DRC | 0 |
| Innovus antenna | 0 |
| 最大延迟 SDF 后仿 | PASS，32,226 cycles |
| 后仿中的 setup/hold violation | 0 |

这说明：当前网表在本次 TT 条件下能完成测试程序，CPU、总线、SRAM 和 NPU 的端到端功能一致；时钟树不再是假连接，STA 也不是“0 paths”的无效结果。

但下面的问题仍然阻止把它称为可流片版图：

- `VDDPST`、`VSSPST` 在 LEF 抽象检查中仍显示 `no routing`。课程流程依赖 IO PAD/Filler/Corner 原生 GDS 电源轨的几何拼接；这必须在合并 GDS 上通过 LVS/连通、IR/EM 和 ESD 检查，不能仅凭 LEF 目检放行。
- `checkPlace` 列出 34 个 IO 实例 overlap；逐项核对后均是课程要求的 Wire Bond PAD 与对应 IO PAD 的刻意 M4 叠接，不是相邻 IO 单元互相侵占。
- 27.31% 触发器没有完整 scan-chain 定义，DFT 未闭环。
- 只完成 TT/1.8 V/25 °C 单角分析，没有 SS/FF、多电压、多温度 signoff。
- 还没有完成 foundry Calibre DRC、LVS、PEX，也没有 IR-drop/EM、功耗、形式等价和完整 DFT 验证。

因此本次结果应表述为：**完整的课程级 P&R 和 SDF 功能后仿已经跑通；物理 signoff 尚未完成。**

## 2. 输入与库

### 2.1 RTL 与软件

- ChipDesign Git commit：`9d25b0965ba4c190c4ec352d4f4b9b5b5920273b`
- SoC 顶层：`hardware/soc/rtl/my_soc_top.sv`
- NPU 默认实现：`FUSED_CORE=1`
- 后仿固件：`hardware/soc/sim/sw/chipdesign_npu_test.hex`
- PASS 地址：`0x8000_1FE0`，对应 SRAM word `0x7F8`
- PASS 值：`0xC0DE_C0DE`

### 2.2 工艺与 SRAM

标准单元、IO PAD 和工艺文件来自课程服务器的 TSMC 180 nm PDK。SRAM 使用用户提供的：

`RA1SHD_2048x32M8.zip`

该压缩包包含：

- TT/SS/FF Liberty timing library
- Verilog 仿真模型
- abstract LEF/antenna LEF
- CDL

压缩包**不含 GDS**。本次 GDS merge 使用了课程 Lab 4 模板中的同名 `RA1SHD_2048x32M8.gds`。正式流片前必须由课程/工艺负责人确认该 GDS 与用户给出的 Liberty、LEF、CDL 属于同一版 macro release，不能仅凭同名推断。

SRAM 尺寸为 `554.595 µm × 723.85 µm`，约 `0.4015 mm²`。

### 2.3 工具版本

- Cadence Genus `19.17-s144_1`
- Cadence Innovus `21.16-s078_1`
- Cadence Xcelium `21.09-s013`

本次运行中三者 license 均成功 checkout。

## 3. RTL 基线

在替换 ASIC SRAM、综合和布局布线前，先用 Xcelium 完成了 7 项 RTL 回归：

1. requant：通过 144 个 corner case 和 20,000 个随机乘法测试。
2. asymmetric capacity：通过。
3. generic 4096 capacity：通过。
4. 集成 SoC：32,226 cycles 后通过。
5. NPU DMA：725 cycles 后通过。
6. SRAM pipeline latency=3：通过。
7. SRAM legacy latency=1：通过。

原始证据见 `work/asic-postlayout-20261016/rtl_regression.log`。

## 4. 映射综合

### 4.1 约束

- `core_clk`：20 ns
- 输入/输出 delay：2 ns
- synthesis clock uncertainty：0.2 ns
- 标准单元：TSMC 180 nm `tcb018gbwp7t`
- SRAM：TT 1.8 V 25 °C Liberty，保留为 hard macro

### 4.2 面积

| 层次 | 单元数 | 面积 (µm²) |
|---|---:|---:|
| `my_soc_top` | 94,601 | 3,382,204.658 |
| fused NPU core | 69,494 | 2,300,905.435 |
| CPU core | 11,337 | 257,319.148 |
| requant lane | 2,852 | 59,713.831 |

这里的总面积是标准单元面积，不包含 SRAM hard macro、IO PAD、布线和空白区。NPU 占标准单元面积约 68%，是后续 PPA 优化的主要对象。

### 4.3 综合时序

- WNS：`+7.521 ns`
- 目标周期：`20 ns`
- 最慢路径：CPU ID/EX 相关 ALU/shift 数据通路
- `check_design`：无 unresolved reference、无 empty module

综合阶段使用 wireload/估算互连；最终判断应以后布局 RC 时序为准。

## 5. Floorplan、CTS 与布线

### 5.1 版图规模

- core：`2800 µm × 2800 µm`
- die：约 `3100.16 µm × 3100.16 µm`，约 `9.61 mm²`
- SRAM：单宏，放置于 core 内
- post-route placement density：42.983%，加入 filler 后为 100%
- clock tree sinks：
  - `core_clk`：41,201
  - `jtag_clk`：243/244（检查点恢复日志中显示 244）

时钟 sink 数证明 PAD 后的核心时钟已被 CTS 正确识别。最初一次错误运行因 PAD net split 错误导致核心时钟断开、STA 显示 0 paths；该结果已经作废，最终数据来自修复后的完整重跑。

### 5.2 post-route STA

使用 TT/1.8 V/25 °C、提取后 RC、MMMC OCV 模式：

| 检查 | WNS | TNS | violating paths | all paths |
|---|---:|---:|---:|---:|
| Setup | +3.625 ns | 0 ns | 0 | ≈116k |
| Hold | +0.023 ns | 0 ns | 0 | ≈116k |

时序余量为正，但 hold 余量仅 23 ps，且只是单一 TT corner；这个数不能替代 SS/FF 多角 signoff。

### 5.3 物理检查

| 检查 | 结果 | 判断 |
|---|---:|---|
| Innovus `verify_drc` | 0 violations | 通过实现级检查 |
| Innovus process antenna | 0 violations | 通过当前 LEF 规则检查 |
| regular connectivity | 2 | 仅 `VDDPST/VSSPST` LEF 抽象限制 |
| VDD/VSS special connectivity | 0 | 通过 |
| placement | 34 IO/Bond overlaps | 均为预期的封装焊盘叠接 |
| max capacitance | 0 | 通过 |
| max transition | 0 | 通过 |

早期失败版本曾有 766 个 VDD/VSS 连通项。根因不是自动布线算法缺失，而是北/东侧 IO 初始坐标使用了 Die 边界值；`loadIoFile` 按 PAD 深度再次向内缩进，导致 IO/Filler 与核心电源环重叠，Innovus 为避免短路删除了电源环线段。最终修复为：北/东 PAD 初始坐标改为 `Die-115 µm`、注册后逐实例校验 box/orientation 未变化，并把 PAD sroute 选择区限制到 core-facing M2 窗口。重跑后 Powerplan special DRC 为 0，M1 阶段仅有课程参考中的 22 个 VSS 行轨边界 dangling 提示；正式布线后 VDD/VSS connectivity 为 0 问题。

另外，SRAM abstract LEF 引用了当前 6-metal tech LEF 中不存在的 METAL7/METAL8 obstruction，工具忽略了这些 obstruction；SRAM antenna LEF 也缺少部分 pin diffusion-area 信息。这两项都需要库提供方确认。

## 6. 导出物

服务器目录：

`/home/<course-account>/chip-fullstack-2026/ChipDesign_pnr_20261015/pnr/my_soc_top/outputs/manual_final`

| 文件 | 大小 | 用途 |
|---|---:|---|
| `my_soc_top.def` | 193,513,775 B | placement/routing 交换 |
| `my_soc_top.gds.gz` | 29,414,219 B | 版图数据，已 merge stdcell/SRAM/IO GDS |
| `my_soc_top.lef` | 2,524 B | 顶层 abstract |
| `my_soc_top_hier_postpnr.v` | 18,568,310 B | 层次后布局仿真网表 |
| `my_soc_top_flat_lvs.v` | 49,014,702 B | flat LVS 网表 |
| `my_soc_top_tt_io.sdf.gz` | 20,554,311 B | 延迟回标 |
| `my_soc_top_tt_io.spef.gz` | 44,456,048 B | 寄生 RC |
| `my_soc_top.sdc` | 1,612 B | 时序约束 |

`my_soc_top_path_adjust.tcl` 为空，说明本次没有额外 path-adjust handoff 内容。

该 Innovus 版本最初把 PAD 外侧网络保存成 `*_pad`，但没有在 Verilog 中生成它与同名顶层端口之间的 alias，导致导出的电气网表端口悬空。最终服务器上的 hierarchical 和 flat LVS 网表均已把这六个网络规范化为 `clk_i/rst_ni/tck_i/tms_i/td_i/td_o`；原始文件以 `.innovus_raw` 后缀备份。Stage 3 脚本也加入了同样的导出后规范化处理。

## 7. 最大延迟 SDF 后仿

### 7.1 testbench 检查内容

后仿 testbench：`hardware/soc/sim/tb/chipdesign_soc_postlayout_tb.sv`

它执行以下检查：

- 把 `chipdesign_npu_test.hex` 预装到 vendor SRAM 模型的 `mem` 数组；
- 使用与实现一致的 20 ns 时钟；
- 在 `my_soc_top` 实例上回标 `MAXIMUM` SDF；
- 等待软件写入 SRAM magic PASS；
- 检查 NPU IRQ 确实出现过；
- 最多运行 1,000,000 cycles，防止静默卡死。

### 7.2 实际规模与结果

Xcelium elaboration：

- 99,659 module instances
- 449,878 primitives
- 41,565 registers
- 547,646 timing checks
- 354,897 interconnect delay objects

最终结果：

```text
[644670000] POSTLAYOUT_PASS: integrated SoC OK after 32226 cycles
Simulation complete via $finish(1) at time 644670 NS + 0
```

- 功能：PASS
- NPU IRQ：PASS
- 运行时 setup/hold timing violation：0
- Xcelium wall time：2 分 28 秒

### 7.3 后仿警告解释

后仿虽然通过，但 SDF 覆盖并非零警告：

- 8 个 `SDFINF`：SDF 中存在 antenna diode 实例，而仿真网表导出时排除了部分 `ANTENNABWP7T` 实例。
- `SDFNEP` 和 `SDFNET` 各显示 500 条后达到消息上限：Liberty 生成的部分 edge-specific path、recovery/removal check 在服务器提供的 Verilog cell model 中没有完全对应的 specify 项。
- 9 个 `SDFNDP`：少量负 delay 被 Xcelium 截断为 0。
- 5 个 time-zero PAD bus-conflict：IO PAD tie-control 在 0 时刻尚未稳定；复位释放后未再次出现。
- 47 个 glitch-suppression：门级 inertial delay 取消了短脉冲事件；没有导致功能失败或 timing violation。

这些警告意味着本次结果可作为课程级“带互连与大部分 cell delay 的门级后仿”，但不能声称 100% SDF signoff coverage。正式签核需要与 Liberty 完全配套的 foundry Verilog timing model，并让仿真网表包含 SDF 中出现的 antenna diode。

## 8. 可复现入口

主要实现脚本位于：

- `hardware/asic/flow/pnr/scripts/lab4_stage01_floorplan_io.tcl`
- `hardware/asic/flow/pnr/scripts/lab4_stage02_power_place.tcl`
- `hardware/asic/flow/pnr/scripts/lab4_stage03_cts_route_export.tcl`

后仿封装脚本：

`hardware/asic/flow/sim/run_postlayout_xcelium.sh`

在 EDA 服务器上设置：

```bash
export CHIPDESIGN_ROOT=/path/to/ChipDesign_eda
export CHIPDESIGN_PNR_ROOT=/path/to/ChipDesign_pnr
bash hardware/asic/flow/sim/run_postlayout_xcelium.sh
```

本地保留的精简证据位于 `work/asic-postlayout-20261016/`，包括综合报告、最终 STA summary、DRC/antenna/connectivity、SDF annotation log 和 Xcelium 后仿日志。大体积 GDS/DEF/SPEF/SDF/网表只保留在服务器目录中。

## 9. 到真正流片还差什么

建议按以下优先级继续：

1. 在合并 GDS 上验证 `VDDPST/VSSPST` 原生 IO 环连续性，并完成 IO/core 两套电源的 IR-drop、EM 与 ESD 检查。
2. 建立 SS/TT/FF、多电压、多温度 setup/hold MMMC；23 ps 的 TT hold 余量需要特别关注。
3. 补齐 scan chain、scan reorder、ATPG 和 coverage，消除 27.31% flops 未定义问题。
4. 用匹配版本的 SRAM GDS/LEF/CDL/Liberty/Verilog 全套视图重新确认 macro。
5. 跑 Calibre foundry-rule DRC、LVS；随后进行 PEX，并用提取网表/寄生做 signoff STA。
6. 跑功耗、时钟完整性和形式等价验证。
7. 所有 waiver 都应有规则编号、版图位置、原因和负责人批准；不能仅凭 Innovus `verify_drc=0` 放行。

## 10. 最终判定

| 里程碑 | 状态 |
|---|---|
| RTL 功能正确 | 通过 |
| 映射综合完成 | 通过 |
| Floorplan/CTS/route 完成 | 通过课程级检查 |
| 单角 STA | 通过 |
| SDF 门级功能后仿 | 通过 |
| 实现级 DRC/antenna | 通过 |
| 核心 VDD/VSS 连通 | 通过 |
| IO VDDPST/VSSPST | LEF 抽象限制，待 GDS/LVS/IR/EM/ESD |
| IO placement | 通过；34 个 IO/Bond overlap 为预期叠接 |
| 多角 signoff STA | 未完成 |
| Calibre DRC | **FAIL：2424 clusters / 14521 shapes** |
| Calibre antenna | **PASS：0 results** |
| Calibre LVS | **FAIL：INCORRECT** |
| Calibre PEX | 未执行，受 LVS 阻塞 |
| DFT/IR/EM/功耗/formal signoff | 未完成 |
| 可提交流片 | **否** |
