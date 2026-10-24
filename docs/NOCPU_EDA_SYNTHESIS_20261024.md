# No-CPU 可编程 NPU 基线综合与布局布线（2026-10-24）

主力综合目标为本地 `exp/no_cpu`，基础提交
`c24b3428b732c987e6c3e720924f32a8f2073673`，包含冻结时的未提交修改。
服务器使用 `ssh xlab`，即 `s2400012892@10.129.70.18:10416`，节点 `tc-01a0e839fa89`。
先前 `main/5204d3f` 的带 CPU 综合及自动后续作业已经停止，原日志保留。

## 两轮输入

用户最初选择先完成本轮基线，再综合稳定后的最新版本；随后明确当前优先交付之前基线的
PPA 和版图数据。最新版接口/计数器更新暂缓综合，物理实现也使用同一份 14:45 基线。

| 轮次 | 输入 | 状态 |
| --- | --- | --- |
| 基线 | 2026-10-24 14:45（新加坡时间）冻结的工作区，快照 `f9ae8f1094eb` | Genus 完成（15:35）；Innovus 布局布线及导出完成（16:22），exit 0 |
| 最新版候选 | 快照 `54bd13142940`，包括性能计数器及控制接口更新；56 项功能回归通过 | 暂缓综合 |

基线物理实现脚本位于远端 `physical/bin/`，读取基线 `results/my_soc_top.v` 与对应 SDC。
范围为无封装焊盘的核心模块，7 个信号端口、1 个模型 SRAM，保留原 PDK/宏 LEF。
已输出 floorplan、placement、CTS 和 post-route 检查点，以及最终 DEF/GDS、网表、SDC、
SPEF、SDF 和 LEF。以下数据来自实际报告。功耗使用无活动文件的静态估算，Genus 提供
0.05/0.10/0.20 默认翻转系数敏感性，Innovus 提供包含时钟树和布线寄生的估算；
不能等同于实际工作负载功耗或实测功耗。

2026-10-24 15:09（新加坡时间），独立的两 buffer + SRAM 小设计已通过新物理脚本的
导入、floorplan 和供电网格验证：Innovus 0 errors，special DRC 未发现违规，
VDD/VSS 连通性未发现问题。证据在 `physical/smoke/`；这是几何/脚本验证，
不计入完整基线的 PPA 或版图收敛结果。

## 基线综合结果

Genus 在 15:35 正常退出，完成标记存在，包装脚本退出码 0，产物 SHA256 已记录。

| 指标 | 数值 |
| --- | ---: |
| 总单元面积（含模型 SRAM） | 3,554,492.745 µm² = 3.554493 mm² |
| 标准单元面积 | 3,153,049.151 µm² = 3.153049 mm² |
| 模型 SRAM 面积 | 401,443.594 µm² = 0.401444 mm² |
| 实例总数 | 104,995（104,994 标准单元 + 1 SRAM） |
| 时序单元数量 | 43,375 |
| 4 KiB 激活存储面积 | 2,112,219.219 µm²，占总面积约 59.4% |
| NPU core 面积（含 requant） | 975,270.283 µm² |
| 50 MHz 综合 setup slack | +6.364 ns |
| 最差报告路径数据延迟 | 13.162 ns |
| 综合功耗估算（默认翻转系数 0.10） | 441.573 mW |

功耗使用 Genus legacy power engine，在每个独立新进程中重新读入同一份映射网表和 SDC，
概率设 0.5，翻转系数取 0.05 / 0.10 / 0.20，对应总功耗分别为
334.093 / 441.573 / 491.045 mW。中间场景的泄漏为 0.018 mW，
激活存储贡献 302.739 mW。这是无波形、无布线寄生的假设场景估算，不能当作实测或工作负载功耗。
最初连续调用 Joules report_power 的三个结果受活动传播缓存影响而相同，已保留在
`physical/attempt1/reports/power_factor_*.rpt`，不作为翻转率敏感性结果使用。

最差报告路径从 requant 的 `s1_multiplier_reg[3]` 到 `s2_product_reg[62]`。
这里使用 TT、理想时钟及 ZeroWireload，没有布线寄生；不能据此声称布线后已收敛。
综合 SDC 重新读入时，核心时钟仍为 20 ns，JTAG 仍为 1000 ns。

完整物理运行的首次尝试因 Innovus `libCell.area` 属性不受支持，读到标准单元面积 0，
导致 floorplan 偏小并在 placement 利用率检查处停止；错误日志和检查点保留在
`physical/attempt1/`。已改用 LEF cell.size 宽×高求面积，并增加正值检查。
重新计算为 3,153,049.1776 µm²，与综合面积在报告舍入精度内一致。
修正后的核心约 2600.08 × 2600.08 µm，模块边界 2679.6 × 2679.6 µm；
完整基线随后重新运行，最终物理结果见下文。

15:53 完成 placement；独立 `checkPlace` 报告未发现违规，134,661 个实例已放置，
未放置数 0。工具报告 placement density 为 51.33%，包括固定标准单元时为 52.28%。
从实际 instance box 导出的 64×64 密度图已经生成（不把 filler/tap/decap/antenna 算入活动单元密度）；
该图是 placement 数据，不是金属布线或拥塞图。15:53 起进入 CTS，16:22 完成最终导出。
综合机器可读汇总见 `docs/validation/nocpu_baseline_synthesis_20261024.json`。

## 最终布线后 PPA 与版图

最终报告以 `physical/reports/postRoute/` 和 `final_*.rpt` 为准。核心模块在
TT 1.8 V / 25°C、20 ns 核心时钟、1000 ns JTAG 时钟下得到：

| 指标 | 最终值 |
| --- | ---: |
| 核心尺寸 | 2600.08 × 2600.08 µm |
| 核心面积 | **6.760416 mm²** |
| 模块边界面积（含核心外电源环余量，无 pad ring） | 7.180256 mm² |
| 活动标准单元面积（含 CTS/hold/tie，排除 filler/tap/decap/antenna） | 3.186772 mm² |
| SRAM 几何面积 | 0.401444 mm² |
| 活动单元与 SRAM 面积合计 | 3.588215 mm² |
| 活动标准单元数 / SRAM 数 | 107,336 / 1 |
| 标准单元面积 / 非宏核心面积 | 50.11% |
| 工具报告密度（按工具可放置行面积定义） | 51.58% |
| 64×64 几何面积密度图最高格 | 81.94% |
| setup WNS / TNS / 违规路径数 | **+0.821 ns / 0 / 0** |
| hold WNS / TNS / 违规路径数 | **+0.003 ns / 0 / 0** |
| max_cap / max_tran / max_fanout / max_length DRV | 均为 0 |
| 静态功耗估算 | **374.686510 mW** |
| 其中 internal / switching / leakage | 316.560841 / 58.107312 / 0.018357 mW |
| 其中时钟组合单元功耗 | 约 39.22 mW |
| 常规信号/时钟路径中心线总长 | 约 7.208530 m |
| 布线网数（常规 NETS） | 111,589 |
| 未放置实例 | 0 |

独立 `checkPlace`、常规/电源 `verify_drc`、信号/电源 `verifyConnectivity` 和
`verifyProcessAntenna` 均通过，报告未发现违规或连通性问题。全局资源估计仍有
111 个 overflow 单位（H 94，0.02%；V 17，四舍五入为 0.00%），这与最终详细布线 DRC
属于不同检查，不能把全局 overflow 描述为完全为零。信号线主要使用 METAL2–6。

填充后总实例数 340,638，其中 233,301 为本统计排除的物理单元；
`final_placement.rpt` 的 100% 包含 filler，不能作为逻辑利用率。
宏面积采用 LEF instance bbox（与 Liberty 一致）；工具 summary 的宏有效面积另报
388,255.923 µm²，未将其替换为几何面积或混用面积定义。

功耗使用 Innovus static：primary input activity=0.1、sequential activity=0.1，无波形，
包含实际时钟树和 post-route RC；报告确认功耗单位 1 mW、核心周期 0.020 µs、
JTAG 周期 1.000 µs。TIEH/TIEL 缺少 internal power 表的库诊断保留。
该结果与 Genus 的 441.573 mW 使用不同引擎和活动传播，不能将两者差值称为优化收益。

提取使用课程 `t018lo_1p6m_typical.captable`、Innovus postRoute low effort，最终时序计算
启用 SI。当前只检查 TT 角，不是全工艺角时序或晶圆厂签核 DRC/LVS；
hold 最小裕量只有 3 ps，不能据此声称其他 PVT 角也已收敛。未完成门级 SDF 时序回归。

全流程退出码为 0，且 `NOCPU_BASELINE_PHYSICAL_FLOW_COMPLETE` 存在；日志仍保留
14,532 条 warning 计数及 2 条 IMPSP-9099 error 诊断，后者均为映射扫描触发器未定义扫描链。
本轮保持原功能连线、禁用扫描重排，没有伪造扫描链；不能把本次结果描述为“全日志零错误”或
DFT 已完成。

本地成果（相对仓库根目录）：

- `docs/validation/nocpu_baseline_physical_20261024.json`：机器可读物理 PPA、检查和假设。
- `work/xlab-nocpu-eda-20261024/baseline_evidence/analysis/final_layout.png`：实例 bbox 密度图。
- `work/xlab-nocpu-eda-20261024/baseline_evidence/analysis/routed_wires.png`：实际 DEF 常规布线中心线图，排除 special power nets、via 和 patch shapes，不是完整 GDS 层图或 DRC 检查。
- `work/xlab-nocpu-eda-20261024/baseline_evidence/physical/outputs/`：最终 DEF、GDS、SPEF、SDF、LEF、SDC 和网表。
- 远端根目录 `baseline_ppa_layout_evidence.tar.gz` 与 `evidence_manifest.json`：报告、产物、运行脚本及 SHA256 清单，不打包原 PDK 或大型数据库检查点。

60% 利用率复查见 `docs/NOCPU_EDA_DENSITY_60_20261024.md`。同一冻结基线在核心
5.664400 mm² 下完成 TT 物理流程：post-route setup/hold 为 +0.423/+0.004 ns，
常规/电源 DRC、连通性和天线均通过，静态功耗 374.881217 mW。与 50% 基线相比核心
面积减少 16.21%，setup 裕量减少 398 ps，hold 仍有 4 ps；这是已验证的单角候选，
不是多角签核结论。

基线独立目录：

```text
/home/s2400012892/chip-fullstack-2026/ChipDesign_nocpu_eda_20261024_c24b342_wip_f9ae8f1094eb
```

`source_manifest.json` 记录完整基础提交、375 个文件的 SHA256、9 个综合 RTL 输入与冻结时间。
`WORKTREE.patch` 保存冻结时的全部已跟踪文件修改。无 CPU/AXI/DMA 进入综合文件表。
FPGA 顶层及 XDC 的未提交修改包含在快照中，但 ASIC 综合入口仍是 `my_soc_top`，不读取 XDC。
临时编辑器数据、构建产物和私有库未打进源归档。

## 设计和工艺配置

- `hardware/soc/sim/filelists/chipdesign_nocpu.f`，顶层 `my_soc_top`。
- 五算子 ABI 2：Conv、FC、MaxPool、ReLU、Requant，4×8 MAC 阵列。
- 8 KiB 模型存储使用 `RA1SHD_2048x32M8` 硬宏，`NPU_USE_RA1SHD_MODEL`；4 KiB 激活仍为 RTL 存储。
- TSMC180 `tcb018gbwp7ttc` 标准单元库与 SRAM TT 1.8 V/25°C Liberty。
- 核心 20 ns（50 MHz），JTAG 1000 ns（1 MHz），两时钟活动且异步分组。
- 输入/输出预算 2 ns，核心时钟 uncertainty 0.2 ns，Genus CPU 数上限 8。

外部 Genus 驱动在展开后显式设置 SDC ns/pF 单位，生成时钟报告；冻结的源脚本保持原字节。
时钟报告显示 `core_clk=20000 ps`、`jtag_clk=1000000 ps`，uncertainty 为 200 ps。

## 兼容修正与验证

Genus 19 首次读入报 `ret_tile` 先引用后声明。本轮在本地将 `LAT`、`tile_pipe` 和
`ret_tile` 的声明移到首次使用前，没有改变逻辑表达式，随后重新冻结为上述基线。
最初失败的快照 `2354c6bdf804` 和日志仍保留在独立目录。

基线已通过：

- 375 文件远端 SHA256 一致性检查。
- 带实际标准单元/SRAM 库的展开，无 unresolved references、无 empty modules。
- 冻结源码的本地 Icarus 完整通用回归：56 场景，涵盖 4×8、4×4、融合/独立和 JTAG；
  另有 20000 个 Q31 量化向量、存储背压/复位、随机响应延迟及 8 类非法模型和生命周期测试。
- Xcelium 与课程真实 SRAM 模型的功能存储回归（`-notimingchecks`）：背压、复位保持、非对齐读取通过。

56 场景未包含可选真实训练 NPZ 场景，运行该回归时所用本地 Python 未安装 NumPy。
真实 SRAM 测试在启用 specify timing checks 时出现 CEN hold 违例并使 notifier 污染输出；
该失败日志同样保留。关闭 timing checks 的通过仅是功能证据；本轮物理 STA 的 TT hold
检查通过，不替代该存储测试台的时序回归。

## 状态和产物

### 更高布局密度的可行性

可在冻结 RTL 不变的条件下，通过提高目标利用率、调整宏位置/halo、拥塞驱动布局、
时序驱动布线及缓冲器优化来探索更小的核心。当前使用商业 Innovus，能直接调整的是
流程参数和约束；更改其内部布局/布线算法需要工具厂商支持或另行迁移到开放工具。

以最终活动标准单元面积约 3.187 mm²、SRAM 几何面积 0.401 mm²，按
`核心面积 ≈ 标准单元面积 / 目标利用率 + 宏面积` 作初步估算：60% / 65% 对应约
5.71 / 5.30 mm²，相对本轮 6.7604 mm² 约减少 15.5% / 21.5%。这是几何估算，
没有计入密度改变后新增的 CTS/hold 单元、宏 halo、布线通道及 PDN 约束。
**60% 已在独立目录实际验证为 5.664400 mm²，65% 尚未运行**。60% floorplan 与原流程相同，
使用综合网表面积 3.153049 mm² 计算边长，所以上述基于布线后单元面积的估算与实际值略有差异。
原 50% 基线的 floorplan 和 RTL 均保持不变。

是否可用需以完整详细布线后的 50 MHz setup/hold、DRV、DRC、连通性及拥塞为依据。
基线最终 post-route setup/hold WNS 为 +0.821/+0.003 ns；尤其 hold 裕量较小，
不能仅根据早期低拥塞或面积公式承诺更高密度仍然收敛。

```bash
source ~/chip-fullstack-2026/ChipDesign_nocpu_eda_20261024_c24b342_wip_f9ae8f1094eb/env.sh
cat "$CHIPDESIGN_EDA_ROOT/logs/status.json"
tail -n 30 "$CHIPDESIGN_EDA_ROOT/logs/synthesis.log"
```

每轮的 `logs/status.json` 记录源版本/快照、配置、状态、退出码和产物 SHA256。
`logs/synthesis_exit_code.txt` 仅在作业退出后生成，0 且完成标记存在才表示综合通过。
映射成功后 `results/` 将包含网表、SDC、面积、时序和单元统计报告。
综合与物理实现已经完成；物理检查、布线寄生、post-route 时序和功耗见上述最终报告。

不沿用带 CPU 主线的固定宏实例路径、六信号 pad ring 或后仿真测试台。
No-CPU 顶层另有 `irq_o`，PNR 和签核场景需按该版本独立适配。
本地证据和运行脚本位于被 Git 忽略的 `work/xlab-nocpu-eda-20261024/`。
