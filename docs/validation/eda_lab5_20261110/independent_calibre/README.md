# 独立重跑与 Calibre 验证结果（2026-11-10）

已完成全新综合、PnR、CDC、严格 SDF 及后续全部 Calibre 运行。修复首轮发现的 7 处 Wire Bond 间距问题后，最终 GDS 的 DRC 按课程四类单列口径通过，LVS 为 CORRECT，天线与 Wire Bond 为 0 违规。**ERC 仍有 7 个层次／977 个展开结果待人工评审；当前不是完整流片签核通过。**

## 固定条件与最终结果

保持 ABI 5.0、8×8 阵列、core 50 MHz、TCK 1 MHz，die 上限 1.95 × 1.95 mm。源码基线为 `aa778875eed49ada6b7b36bf10b1c0cdce2ad3c9`；新增三文件叠加仅涉及物理布线 keepout 和测试，未修改 RTL、时序例外或验证阈值。

| 指标 | 独立 aa77887 完整重跑 | WB 修复版（最终 PnR） |
|---|---:|---:|
| 综合标准单元面积 | 1,394,641.294 µm² | 同一已核验综合交付 |
| 布线后标准单元几何面积 | 1,430,890.630 µm² | 1,430,574.522 µm² |
| 标准单元实例数 | 63,204 | 63,163 |
| 实际 die | 1940.4 × 1940.4 µm | 1940.4 × 1940.4 µm |
| setup / hold WNS | +0.487 / +0.050 ns | +0.444 / +0.022 ns |
| setup / hold TNS | 0 / 0 ns | 0 / 0 ns |
| CDC、流程物理检查、严格 SDF | PASS | PASS |
| 信号总布线长度 | 5,015,014.47 µm | 4,998,805.67 µm |

综合面积较原始基线降低 22.48%；Liberty 综合面积、布线后实例几何面积及 die 面积是不同口径。两颗 SRAM 保持不变。独立 aa77887 重跑的 11 项物理指标与上一轮最终 ECO 完全一致，见 [比较记录](evidence/independent_comparison.json)。WB 修复版改变了物理布线，单独验收并保留自己的指标。

最终加环物理外框为 `(-15.3,-15.3)–(1955.7,1955.7)` µm，即 **1971 × 1971 µm**，符合课程 1980 µm 加环外框限制；die 约束仍为 1950 µm。完整 GDS 保持 **2000 DBU/µm**，没有经 1000 DBU OA 重量化整片坐标。原生 Seal Ring 单元、15 µm 环结构与四角方向保持，参考边适配为 1941 µm。

## Calibre 最终验收

以下全部检查对应同一最终 GDS/CDL 哈希。规则完整执行，未在 deck 中屏蔽 DRM/ESD/MOM/LUP；四类教学口径只在报告统计时单列。流程依据：[课程 Lab 5](https://full-stack-ai-chip.tianyuj.com/lab-5/)。

| 检查 | 实际结果 | 验收范围 |
|---|---|---|
| 几何 DRC | 523 项；原始 2427 层次／16935 展开；非教学排除项 0 | 按课程四类单列口径通过；原始非零计数保留 |
| LVS | CORRECT | 完整比较两颗 SRAM；7 信号、4 PG、POC 共 12 个端口；PR 容差见下文 |
| ERC | 12 项；7 层次／977 展开 | 待人工评审，未声明 ERC Clean |
| 独立天线 | 62 项／0 违规 | 最终 postDummy GDS |
| Wire Bond | 64 项／0 违规 | PITCH_55_SINGLE、OBV_CUP；原 7 处间距错误归零 |

实际为 **35 个 PAD60LU_OBV**，N/S/W/E 分别 10/11/7/7 个，同边最小节距 155.455 µm。源环和原生 PAD 保留，SRwidth 15 µm、其他工艺数值与原 EXCL 处理保持。CUPVIAT.DN.1_ 等全部 WB 检查执行，未套用旧版库 release-note 豁免。

DRC 原始非零项目：ESD.24g=30、ESD.25g=2384、LUP.3.3g_3.3V=1、LUP.5.3g_3.3V=10、MOM.R.2=1、DRM.R.1=1。CO.R.4、DOD.R.1、M6.A.1、SR.S.1 及金属密度项在最终报告中归零；没有扩大为全部前端规则豁免。

LVS 首先保留默认 PR 容差结果：45 项 IO 电阻属性差异均为 3.47%，没有其他比较错误；模型与实测示例为 248.8 Ω 对应 257.44 Ω。只在项目 deck 的三个 R(PR) 比较分支采用 **3.48%** 后重跑得到 CORRECT，未修改 SPI/CDL 数值、其他器件属性或开启 RC_DECK/ACCURACY。此为有实测证据的教学比较口径，并非模型修正或正式工艺豁免。

## 修复及复核过程

1. aa77887 在全新目录完成全新 Genus 综合、完整 PnR 和严格 SDF；首轮最终 Calibre 的 WB 命中 CB.S.2.M5 两处、CB.S.2.M6 五处，失败 GDS 与报告完整保留。
2. 填充前检查与去除 IO 区域浮空填充的诊断均复现相同 7 处。Innovus 查询定位到激活存储相关的普通信号网：例如一处 M5 线端与 PAD 金属间距为 2.42 µm，低于 WB 2.5 µm 要求。
3. 新增 [Bond PAD keepout](../../../../hardware/asic/optimize/wirebond_keepout.tcl)，由 [route.tcl](../../../../hardware/asic/optimize/route.tcl) 在 CTS 后、全局布线前调用：实际 35 个 Bond PAD 外扩 3 µm，M5/M6 信号禁布区保留 PG 通路，并设独立填充禁区。从独立 CTS 检查点重新布线、优化、提取，重新核验时序、CDC、物理检查和 SDF。
4. 在项目 GDS 副本中按精确坐标修复一个 IO PIMP 矩形，覆盖越界接触孔并与相邻 NIMP 边界衔接；CO.R.4 从 1 层次／8 展开降为 0。共享 PDK、OD/Poly、SRAM 电路及验证阈值均未修改。
5. 修复版 prefill WB 64 项／0 后，重新生成普通金属 M1–M6 及 OD/PO Dummy；普通 M6 小填充 0.8×0.8 µm，THICK_40K_M6 关闭。使用干净 preDummy 合并为 `my_soc_top_postDummy`，重新执行全部最终 Calibre。

## 严格 SDF 与时序边界

修复版 SDF 在 core 50 MHz／TCK 1 MHz 下通过 41 条 JTAG 命令、340 个 burst，连续两次非零算子链推理及复位恢复正确，未豁免的 timing violation 或 SDF 回标错误为 0。

仅在预定的 6 个异步捕获／复位同步单元禁用 36 项 timing check，其余 **90,764 项回标率 100%**；req/ack 第二级检查保留。路径延迟回标 98.46%，未回标部分按实际库模型逐项审计为 ifnone 默认弧及 SRAM 无目标 negedge period；5 条 time-0 输入 PAD 初始化冲突按既有窄范围策略保留并接受，没有通用放宽。

setup +0.444 ns、hold +0.022 ns 是**当前 TT 条件、PnR 导出版本**的结果，hold 余量较小。后续 Seal Ring／IO implant／工艺 Dummy 的最终 GDS 已做 Calibre 检查，但未重新执行该最终填充版图的 RC 提取及 STA/SDF；多工艺角、IR/EM、DFT 与人工签核仍需独立证据。

## ERC 人工评审清单

| 规则 | 层次／展开 | 定位与待核对内容 |
|---|---:|---|
| mnpg | 4／832 | 代表标记位于 IO 供电 PAD；核对原生 IO MOS 的 power/ground 端与库原理图意图 |
| floating.nxwell_float | 1／72 | IO well 标记；核对实际 well/tap 网络和库定义 |
| floating.psub_float | 1／1 | Seal Ring 外侧衬底区域；核对原生环及装配隔离要求和验收口径 |
| npvss49 | 1／72 | IO ntap 接 GROUND 的标记；核对源库结构、实际 PG 连接与工艺要求 |

- [ ] EDA／工艺库负责人逐项打开最终 SVDB/RVE 标记，记录结论及证据。
- [ ] 核对 VDD/VSS/VDDPST/VSSPST 四网真实连接；本次未新增虚连或伪造接地标签。
- [ ] Seal Ring 保持装配禁布带；本次未跨隔离区画金属连接以消除 psub 标记。
- [ ] 填写签核人、日期、最终输入哈希及所依据的正式接受条件。当前这些残项未获正式豁免。

## 文件与复现索引

- [公开结果摘要](evidence/public_summary.json)：仅包含可公开的配置、指标和签核边界。
- [最终输入／交付物哈希复核](evidence/final_wb_fixed/final_audit.json)：哈希对应的 GDS/CDL 等完整交付物保留在授权 EDA 环境，不随仓库发布。
- [最终 ERC 代表标记坐标](evidence/final_wb_fixed/logs/final_erc_geometry.json)：保留人工评审范围，不构成 ERC Clean 声明。
- `evidence/first_final_wb_failed/` 保留首轮最终 WB 失败和定位资料；`evidence/synthesis/`、`evidence/pnr/` 为未施加 WB 修复前的全新独立实现证据。
- [实际复现脚本及说明](reproduction/README.md)：脚本需要授权 PDK/EDA 环境和实验目录，不能在 GitHub checkout 中直接运行。
- 原始 Calibre/Innovus/Virtuoso `work/` 目录、PDK、GDS、CDL、SPEF、SDF、数据库和工具日志不公开；对应摘要与哈希保留用于审计。

公开仓库不保存远程服务器路径、许可证文件或物理设计数据库。`formal_tapeout_signoff` 仍为 `false`，请以 [公开结果摘要](evidence/public_summary.json) 的剩余项为准。
