# ChipDesign ASIC Signoff 状态报告

日期：2026-10-16  
设计顶层：`my_soc_top`  
RTL 基线：`9d25b0965ba4c190c4ec352d4f4b9b5b5920273b`  
工艺假设：TSMC 180 nm、1P6M、1.8 V core / 3.3 V I/O  
最终判定：**NO-GO，尚不满足流片 signoff 条件**

> 2026-10-17 复查：在独立候选版中加入 Innovus 自动 metal fill 后，四项金属密度违规已清除；Calibre DRC 从 2424 降为 **2420**，antenna 仍为 **0**，LVS 仍为 **INCORRECT**。下文第 9 节记录增量结果。原始 GDS 和 2026-10-16 数据仍保留作对照。

## 1. 执行摘要

本轮已经在课程服务器上实际执行了 Innovus 实现级检查、Calibre foundry DRC、Calibre antenna 和 Calibre LVS，并修复了六个信号 PAD 与顶层端口物理断开的实现错误。修复后的 GDS SHA-256 为：

`fc2ce73876688581c71970f0a183ed60187b24e08b5f20c689170bfacfe1112c`

不能签核的直接原因是：

1. Calibre foundry DRC 有 `2424` 个结果簇（`14521` 个被标记图形），不是 DRC-clean。
2. Calibre LVS 结论为 `INCORRECT`，仍有 I/O 库相关拓扑差异、一个 `POC` 全局端口差异，以及 42 个 I/O 电阻参数误差。
3. LVS 未通过，因此不能进行可信的 signoff PEX。
4. 只有 TT/1.8 V/25 °C 单角 STA；SS/FF、多电压、多温度 signoff 尚未建立。
5. 未完成 metal fill、IR-drop/EM、完整功耗、DFT/ATPG、形式等价和封装/ESD 评审。

课程级 P&R、单角 STA 和最大延迟 SDF 后仿均已通过，但它们不能替代 foundry signoff。

## 2. Signoff 矩阵

| 检查项 | 工具/条件 | 结果 | 判定 |
|---|---|---:|---|
| RTL 回归 | Xcelium | 7/7 | PASS |
| post-route setup | Innovus，TT/1.8 V/25 °C | WNS `+3.625 ns`，TNS `0` | 单角 PASS |
| post-route hold | Innovus，TT/1.8 V/25 °C | WNS `+0.023 ns`，TNS `0` | 单角 PASS |
| 最大延迟 SDF 后仿 | Xcelium | 32,226 cycles，功能 PASS | PASS，但 SDF 覆盖有警告 |
| 实现级 DRC | Innovus | 0 | PASS |
| 实现级 antenna | Innovus | 0 | PASS |
| signal PAD 顶层连接 | Innovus + Calibre LVS 端口统计 | 6 个信号已修复 | PASS |
| foundry antenna | Calibre v2022.3_17.8，57 checks | 0 | PASS |
| foundry DRC | Calibre v2022.3_17.8，502 checks | 2424 clusters / 14521 shapes | **FAIL** |
| LVS | Calibre v2022.3_17.8 | `INCORRECT` | **FAIL** |
| PEX | Calibre xRC/等效流程 | 未执行 | BLOCKED BY LVS |
| MMMC STA | SS/TT/FF + 电压/温度角 | 仅 TT | NOT DONE |
| IR-drop / EM | Voltus 或等效 signoff 工具 | 未执行 | NOT DONE |
| metal fill | foundry fill deck | GDS 中为 0 | NOT DONE |
| DFT/scan/ATPG | 对应 DFT 工具 | 未闭环 | NOT DONE |
| 最终 tapeout | 全部签核项 | — | **NO-GO** |

注意：Calibre 进程退出码为 0 只表示工具正常完成，不表示版图通过；通过与否必须读取 DRC 结果数和 LVS 报告结论。

## 3. 已修复的顶层 I/O 连接错误

原 floorplan 脚本先创建 `${port}_pad`，再调用 `attachModulePort`。该命令会把端口所在网络自动改名为端口名，但脚本仍使用旧字符串 `${port}_pad` 去连接 PAD，导致每个信号形成两个互不连接的网络：

- 顶层端口连接 `clk_i/rst_ni/tck_i/tms_i/td_i/td_o`；
- PAD 的 `PAD` 端连接孤立的 `*_pad` 网络。

修正包括：

- `attachModulePort` 后重新查询顶层端口实际所在网络，再连接 I/O cell 的 `PAD` 端；
- 在 PDDW0204CDG 的真实 `METAL4` PAD 引脚范围内创建六个 0.4 µm × 0.4 µm 顶层物理 pin；
- 北侧 R180 PAD 使用 y=`3098 µm`，南侧 R0 PAD 使用 y=`2 µm`，避免先前错误放在 METAL6 内部区域造成 6 个短路。

修复结果：

- Innovus DRC：0；
- Innovus antenna：0；
- Innovus regular connectivity 只剩 `VDDPST/VSSPST` 的 LEF 抽象 `no routing` 信息；
- Calibre LVS 从“layout 4 ports / source 11 ports”改善为“layout 10 ports / source 11 ports”；
- 六个信号端口不再列为 missing，剩余的唯一端口差异是内部 I/O 偏置全局网 `POC`。

永久修正在 `hardware/asic/flow/pnr/scripts/lab4_stage01_floorplan_io.tcl` 中；`work/asic-postlayout-20261016/pnr_experiments/signoff_io_eco.tcl` 是本次最终数据库验证用的 ECO 脚本。

## 4. Calibre DRC 结果

使用 PDK 包内 `TechFiles/Calibre_DRC/calibre.drc.6m8k`，GDS 以 deck 要求的 1000 DBU/µm 重新导出。非零规则如下：

| 规则 | 结果簇（图形数） | 含义/判断 |
|---|---:|---|
| `CO.R.4` | 1 (8) | 特定电阻标记区内 contact 未被 NP/PP 完全覆盖 |
| `M2.R.1` | 1 (1) | M2 全芯片密度低于 30% |
| `M3.R.1` | 1 (1) | M3 全芯片密度低于 30% |
| `M5.R.1` | 1 (1) | M5 全芯片密度低于 30% |
| `M6.R.1` | 1 (1) | M6 全芯片密度低于 30% |
| `ESD.24g` | 30 (180) | ESD 器件 drain 侧 RPO 宽度不足 1.95 µm |
| `ESD.25g` | 2384 (14304) | ESD 器件 source 侧 poly 到 contact 间距不足 0.5 µm |
| `LUP.3.3g_3.3V` | 1 (6) | 3.3 V I/O 相关 latch-up 间距检查 |
| `LUP.5.3g_3.3V` | 3 (18) | 3.3 V I/O 与内部器件的 latch-up 间距检查 |
| `DRM.R.1` | 1 (1) | deck 的 DRM 人工复核提醒 |

密度错误与 GDS 中 `Metal Fills = 0` 一致，必须使用经课程/工艺方确认的 metal fill deck 或脚本处理。大量 ESD/LUP 结果集中在 I/O 库区域，结合当前 GDS 为 `tpd018nv_280a`、可用 SPICE 为 `tpd018nv_270a`，高度怀疑存在 deck 选项或库版本配套问题；在拿到课程指定的 process option 和匹配库之前，不能擅自 waiver。

## 5. Calibre LVS 结果

Calibre 完整提取并比较了约 94.5 万个变换后实例，但最终报告为：

`LVS completed. INCORRECT.`

变换后的网络数量已经一致（layout/source 均为 581,677），主要剩余项为：

- 端口：layout 10、source 11，source 多出全局端口 `POC`；
- 实例：layout 945,360、source 945,336，相差 24 个，差异集中在 I/O 单元内部的 `_sup2v/_nor2v/_pdw2v` 等等效结构；
- 26 个列出的不匹配网络，主要涉及 I/O 内部网络和 tie 网络；
- 42 个 I/O poly resistor 属性误差，全部约 `3.47%`，超出 deck 当前 `2%` 的比较容差。

这说明核心数字逻辑和 SRAM 的大规模结构基本匹配，当前失败点主要位于 I/O/GDS/SPICE/deck 的配套边界，但仍然是正式 LVS 失败。不能通过关闭 `LVS GLOBALS ARE PORTS` 或放宽电阻容差来伪造 PASS；这些改动必须由 foundry/课程负责人确认。

## 6. 为什么没有继续做 PEX 与最终 STA

寄生提取必须建立在“版图连接与原理图连接一致”这一前提上。当前 LVS 仍失败，继续得到的寄生网表可能对应错误拓扑，无法作为 signoff 依据。因此本轮正确的工程结论是把 PEX 标为 `BLOCKED BY LVS`，而不是生成一个不可信的时序数字。

服务器上已确认 Calibre、Genus、Innovus、Xcelium 可正常取 license；没有发现独立的 Quantus、Tempus 或 Voltus 命令。Calibre xRC 模式存在，但当前应先解决 LVS。IR/EM 还需要配套技术文件、供电模型和活动/电流数据，不能仅靠现有 GDS 自动完成。

## 7. 达到真正 signoff 的最短路径

1. 向课程/PDK 负责人确认：DRC/LVS deck 的 process switches、`MIX_MODE`、3.3 V I/O 选项，以及 `tpd018nv_280a` GDS 对应的准确 CDL/SPICE 版本。
2. 用匹配的 I/O source view 重跑 LVS，明确 `POC` 应作为内部 global 还是顶层端口；不得自行关闭检查。
3. 使用官方 metal-fill 流程补齐 M2/M3/M5/M6 密度，重新跑 DRC、antenna 与 LVS。
4. 对 ESD.24g、ESD.25g 和 LUP 结果逐项确认：若是库已签核区域，应取得正式 waiver/black-box 方法；若是 deck 选项错误，应修正选项；若是真实违规，则更换 I/O cell 或版图。
5. LVS 正确后执行 PEX，建立 SS/TT/FF、多电压、多温度 RC/timing corners，重跑 setup/hold/SI。
6. 补做 IR-drop/EM、功耗、DFT/scan/ATPG、形式等价、seal ring 与封装/ESD signoff。
7. 只有当所有强制项 PASS，且每个 waiver 都有规则号、位置、原因和审批人时，才能输出 tapeout GO。

## 8. 证据位置

本地精简证据位于：

`work/asic-postlayout-20261016/calibre-signoff/`

关键文件：

- `my_soc_top.drc.summary`：502 条 DRC rule check 及 2424 个结果；
- `my_soc_top.antenna.summary`：57 条 antenna check、0 结果；
- `my_soc_top.lvs.report`：LVS `INCORRECT` 及对象差异；
- `my_soc_top.lvs.report.ext`：版图提取报告；
- `calibre_erc.sum`：LVS/ERC 摘要；
- `innovus_signoffECO_drc.rpt`：ECO 后 Innovus DRC 0；
- `innovus_signoffECO_connectivity.rpt`：仅 VDDPST/VSSPST 两条抽象 routing 信息。

服务器上的最终候选 GDS：

`/home/<course-account>/chip-fullstack-2026/ChipDesign_pnr_20261015/signoff/calibre_drc/my_soc_top_signoff.gds.gz`

该文件是诊断候选版，不是可提交 foundry 的 signed-off GDS。

## 9. 2026-10-17 增量验证

在原始 signoff ECO 数据库的副本上，Innovus `addMetalFill` 对 M2/M3/M5/M6 插入了 `776,394` 个填充图形。新的候选 GDS 位于服务器：

`/home/<course-account>/chip-fullstack-2026/ChipDesign_pnr_20261015/signoff/metal_fill_trial/my_soc_top_filled.gds.gz`

| 检查 | 填充前 | 填充后 |
|---|---:|---:|
| Innovus DRC | 0 | 0 |
| Calibre DRC | 2424 | **2420** |
| Calibre antenna | 0 | **0** |
| Calibre LVS | INCORRECT | **INCORRECT** |

四项已清除的是 `M2.R.1`、`M3.R.1`、`M5.R.1`、`M6.R.1`，均为低于 30% 的全芯片金属密度错误。当前仍有 `CO.R.4` 1 项、`ESD.24g` 30 项、`ESD.25g` 2384 项、`LUP.3.3g_3.3V` 1 项、`LUP.5.3g_3.3V` 3 项，以及仅为复核提醒的 `DRM.R.1` 1 项。

为定位错误来源，分别以原厂 I/O GDS 中的单个宏作为 Calibre DRC 顶层运行相同 deck：

- `PDDW0204CDG` 信号 PAD 宏单独产生 `ESD.24g` 30 项和 `ESD.25g` 2384 项；
- `PVDD2CDG` 电源 PAD 宏单独产生 `CO.R.4` 1 项。

因此这些主要违规可在未放入 SoC 的原厂 I/O 单元上复现。仍需课程/PDK 方确认 I/O 库与 deck 版本、guideline 适用范围及正式处理方式；单独关闭 `GUIDELINE_ESD` 或修改图形会掩盖真实待判定项。四项 LUP 结果在完整芯片上出现，至少一项坐标位于南侧信号 PAD 内，尚未取得可放行依据。

LVS 诊断还验证了：显式设置 `LVS GLOBALS ARE PORTS NO` 可把 `POC` 视作内部全局网，使双方端口数都变为 10；内部拓扑不匹配和 42 个电阻属性误差仍在，结论仍为 `INCORRECT`。服务器提供的 `tpd018nv_280a_lpe.spi` 是带寄生的模型，直接作为 LVS source 时缺少 `RPPO1RPO` 等依赖定义，Calibre 给出 `NOT COMPARED`。`tpd018nv_270a/tpd018nv_3.spi` 又缺少本设计使用的若干电源 PAD subckt，亦无法代替原 source。

最新本地证据：`work/asic-postlayout-20261016/calibre-signoff/my_soc_top_filled.drc.summary`、`my_soc_top_filled.antenna.summary`、`my_soc_top_filled.lvs.report`、`io_signal_pad.drc.summary`、`io_power_pad.drc.summary`。填充后寄生与时序尚未重新签核，不能直接沿用填充前 STA 数字作为该候选版的最终结果。

## 10. Foundry dummy-metal 与 PEX 追加尝试（2026-10-18）

服务器上还找到了 TSMC `Dummy_Metal_Calibre_0.18um.215a` 官方插入 runset。使用原始未填充 GDS、prBoundary 作为芯片窗口，在独立目录成功生成约 `4,301,276` 个 dummy-metal 图形。将其作为独立子单元并入候选 GDS 后，Calibre 能正常读取；该 GDS 的完整 DRC 为 **195,218** 项，其中 `M6.A.1` 最小面积错误 **192,797** 项，`M2.R.1` 密度错误仍为 1 项，antenna 仍为 0。故这版不能采用。当前使用的主 DRC deck 为 Rev 2.14a，而 dummy-metal runset 为 Rev 2.15a；这种组合需要工艺方确认。仅凭“官方填充生成成功”不能判定其与当前 DRC deck 配套。

已实际测试 Calibre xRC：`calibrexrc` 许可证成功取得。直接用普通 LVS 数据库启动 `-xrc -pdb` 时，Calibre 拒绝该数据库，要求先用 `MASK SVDB ... XRC` 生成 xRC 型 PHDB。随后按 PDK 的 `RC_DECK` 开关尝试重新生成，计算量显著增大；由于当前 LVS 本身仍为 `INCORRECT`，本轮停止了该诊断运行，没有产生可签核的 PEX 网表。此前 Innovus 产生的 SPEF 是实现级 RC 提取，不能替代 Calibre signoff PEX。

本地新增证据：`foundry_dummy_metal.sum`、`my_soc_top_foundry_dummy.drc.summary`、`my_soc_top_foundry_dummy.antenna.summary`、`calibre_xrc_pdb.log`，均位于 `work/asic-postlayout-20261016/calibre-signoff/`。原厂填充版 GDS 与 Innovus 填充版 GDS 分别保留在服务器的独立目录，均未覆盖原始候选版。
