# ABI 5.0 NPU 综合面积与尺寸门禁分析

日期：2026-11-10（Asia/Shanghai）。对象为整合后的 INT4／ABI 5.0、8×8 NPU。综合冻结源为 `7fd9789`，其生产 RTL 与 `8c16462` 完全相同，见 [源码哈希](production_rtl_sha256.json)。本报告不包含正在试验的面积优化候选。

**结论：Genus 映射综合及综合阶段 CDC 审核通过；面积不满足当前 1.95 × 1.95 mm die 上限，PnR 在启动前被门禁拦截。** 当前尚无该版本的已布线时序、SDF、最终 GDS 或 Lab 5 物理签核结论。

## 综合配置与原始证据

- 服务器：`ssh soc`；工具：Genus 19.17-s144_1。
- 工艺／库：TSMC180，标准单元与 SRAM 的 TT、1.8 V、25°C 视图。
- 核心时钟：20 ns（50 MHz）；JTAG 时钟约束：1000 ns；时钟不确定度：0.2 ns。
- 两颗 `RA1SHD_2048x32M8`；关闭波形与逐算子 trace，保留全局计数器。
- 原综合在 `ChipDesign_abi5_20261110_7fd9789/work/abi5_r1/synthesis` 完成。后续 PnR 驱动在 `8c16462` 工程内，通过原始源码／库／交付物哈希、时钟及 CDC 审核后导入同一份网表，未重新映射或改写网表。
- [完成状态](synthesis/status.json)、[实际工具退出码](synthesis/tool_exit.json)、[完整综合日志](synthesis/console.log)、[原始输入清单](synthesis/rtl_sources.json)、[交付物哈希](synthesis/handoff.json)、[导入记录](synthesis/import.json)。

工艺库、宏模型和完整映射网表保留在服务器；本次上传报告、约束、审计数据及哈希，不上传 PDK 库内容。

## 面积结果

原始数据：[Genus 面积报告](synthesis/area.rpt)、[机器可读面积](synthesis/area.json)。

| 项目 | 面积（µm²） | 面积（mm²） |
|---|---:|---:|
| 标准单元 | 1,799,016.883 | **1.799017** |
| 两颗 SRAM 宏 | 802,887.188 | **0.802887** |
| 单元与宏合计 | 2,601,904.071 | **2.601904** |

映射实例共 70,105 个，其中 SRAM 宏 2 个。以上为库面积总和；实际 die 还需要容纳放置空白、IO／电源结构、时钟树和布线通道。IO/PAD、CTS 缓冲及后续修复单元尚未在本综合阶段加入。

### 层级与寄存器拆解

原始数据：[寄存器与组合逻辑拆解](analysis/area_breakdown.json)、[拆解日志](analysis/area_breakdown.log)。脚本按映射网表的寄存器名和实际 Liberty 面积累计；标准单元累计值与 Genus 总值相差不足 1 µm²。

| 层级／类别 | 面积（µm²） | 占标准单元面积 |
|---|---:|---:|
| NPU 核心 `i_npu`（含下列子模块） | 1,542,231.168 | 85.73% |
| 张量有效区查找 `i_tensor_lookup`（核心内部） | **548,799.991** | **30.51%** |
| 两个 requant lane 合计（核心内部） | 58,576.717 | 3.26% |
| JTAG transport | 25,475.296 | 1.42% |
| 全设计触发器累计 | 1,058,068.838 | 58.81% |
| 全设计组合单元累计 | 740,948.051 | 41.19% |

层级行包含子模块，不能与其子模块再次相加。末两行则为互斥的标准单元类别。

主要寄存器组如下：

| 寄存器组 | 位／FF 数 | 映射面积（µm²） |
|---|---:|---:|
| 查找表 `records` | **8,704** | **458,568.5** |
| K 累加 `psum` | 2,304 | 约 121,400 |
| 结果槽 `results` | 1,024 | 约 42,700 |
| 性能计数 `perf_count` | 683 | 约 40,200 |

最直接的候选来源是查找表：`MAX_OPS=128` 时，`$clog2(MAX_OPS+1)=8`，原实现按地址宽度分配了 **256 × 34 bit**，而输入记录加 128 条描述符输出只需要 129 项。正在评估精确深度及相关实现优化；本报告不预先声称其完整综合收益或物理通过。

## 1.95 mm 尺寸门禁

依据课程 [Lab 5](https://full-stack-ai-chip.tianyuj.com/lab-5/) 的原始 die 上限，当前流程保留 1.95 mm 上限及 floorplan 对齐余量。按当前 150 µm IO 边距和几何公式：

```text
core_side ≈ ceil_to_20um(sqrt(标准单元面积 / 目标利用率 + SRAM 宏面积))
die_side  = core_side + 2 × 150um
```

两宏的 halo／通道下限也会参与几何检查。以下是流程计算的目标尺寸，实际放置密度尚需布局数据库测量：

| 目标标准单元利用率 | 估算 die 边长 |
|---|---:|
| 60% | 2,260 µm |
| 65% | 2,200 µm |
| 70% | 2,140 µm |
| 75% | **2,100 µm** |

四个点均超出上限，因此没有启动 Innovus 完整布局布线，见 [实际门禁状态](analysis/pipeline_status.json)。

在当前对齐和边距条件下，最大请求 core 边长为 1,640 µm；以 75% 目标利用率估算，标准单元预算约 **1,415,034.609 µm²**。相对当前值，至少需减少 **383,982.274 µm²，即 21.34%**。实际设计还需要为 CTS、保持时间修复和布线留余量，不能把这个下限当作签核保证。计算数据见 [area_budget.json](analysis/area_budget.json)。

用户已选择保持尺寸上限并继续优化 RTL 面积；不会通过扩大 die、减少 8×8 阵列或取消功能检查来消除门禁。

## 综合时序与 CDC

[综合时序报告](synthesis/timing.rpt)中最差 setup 路径为：

```text
起点：i_control/i_npu/add_second_reg/CP
终点：i_control/i_npu/req_addr_reg[13]/D
setup slack：+7.049 ns
```

这是映射后、布局布线前的库／线负载估算；没有提取后的线网和时钟树，不能替代 routed STA，也不能与 FPGA 的 +0.533 ns 直接比较。Genus 当前审核未提供完整 hold 签核结论。

[综合 CDC 审计](synthesis/cdc_paths.tsv)包含 72 条请求 payload 路径、37 条响应 payload 路径及 2 条同步器级间 setup 路径，均有非负 slack；payload 最小 slack 为 **19.5817 ns**，对应 20 ns bundled-data 约束。路由后最大延迟、同步器级间 setup/hold、RDC 及物理保护仍需重新核验。

## 后续验证状态

| 工作 | 当前状态 |
|---|---|
| 环境、许可证、工艺输入预检 | 已完成 |
| 当前基线映射综合、面积分析、综合 CDC | 已完成 |
| 面积优化候选 | 进行中，未纳入本报告的基线结果 |
| 匹配源的 PnR／提取 STA／SDF | 面积门禁解决后执行 |
| Seal Ring／Dummy 后 DRC、LVS/ERC、最终天线、Wire Bond PAD | 待同源最终版图；尚未完成 |

报告与附件的校验值见 [synthesis_report_sha256.json](synthesis_report_sha256.json)。
