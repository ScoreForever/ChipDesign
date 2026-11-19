# Week 3 全栈工程进展汇报（组会幻灯片，20 页）

接 [`../abi5`](../abi5)。本幻灯片以**自底向上**的工程主线汇报本周进展：
**架构演进与 RTL 验证 → 统一 ASIC 后端与完整 Lab 5 物理签核 → 主机通信优化与 EGo1 FPGA 实板验证 → 六场景应用软件闭环与后续计划**。
模板为 [minimalist-pku-beamer-2026](https://github.com/rexera/minimalist-pku-beamer-2026)（MIT，见 `LICENSE.template`），
去掉顶部章节导航行，保留 `\AtBeginSection` 章节分隔页，全册共 20 页。

## 字体与编译

为兼容网页/IDE `pdf.js` 查看器（避免 CFF/CID OTF 中文字体渲染空白），`main.tex` 按平台选用 **TrueType（`glyf`）中文字体**：
- macOS：`Songti SC`
- Windows / WSL：`/mnt/c/Windows/Fonts/msyh.ttc`（Bold 使用 `msyhbd.ttc`）
- 纯 Linux 回退：`/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`

```bash
python3 make_data.py          # 生成 data/（数字宏 numbers.tex 与表格 / 图表片段）
latexmk -xelatex main.tex     # 生成 main.pdf（20 页，TrueType CJK 嵌入）
python3 make_pptx.py          # 生成 main.pptx（每页一张图，文字放在备注；需 pymupdf、python-pptx）
./clean.sh                    # 清理中间文件
```

## 页面结构（20 页）

1. 封面（PKU-emblem-names）
2. 本周全栈工程总览（四支柱 + 已闭环 / 剩余收尾对照）
3. **第一章分隔页**：架构演进与 RTL 验证
4. `npu_tensor_lookup` 从触发器移入激活 SRAM（架构与时序约束）
5. 七模型匹配基线回归与故障注入（周期变化与 7/7 新增故障注入检出）
6. **第二章分隔页**：统一 ASIC 后端与完整 Lab 5 物理签核
7. 综合与物理实现对比（对比 11-11 基线，统一 `bf75486` 单一 GDS）
8. 物理版图与 WireBond 禁布区（`diag70_h25` 版图四联图、SDF 与 LEC）
9. 4-Corner MMMC 时序、SRAM 接口与 452 条 CDC 闭合
10. 窗口化 100% 标注 VCD 功耗与算子融合节能（55.98% 节能）
11. Lab 5 物理版图工程（`postDummy.gds`，`2000 DBU/µm`：Seal Ring / Dummy / WireBond 禁布）
12. Lab 5 全引擎同源签核（DRC / `CO.R.4` 修复 / 全 SRAM nmLVS / Antenna / WireBond）
13. Lab 5 ERC 晶体管级深度审计（`7 / 977` 逐管核账，设计缺陷 0）
14. **第三章分隔页**：主机通信优化与 EGo1 FPGA 实板验证
15. ABI 5.0 8×8 @ 50 MHz EGo1 实板验证与 JTAG 频率扫描（2026-11-11，4,116 次推理）
16. 主机 JTAG Single-RPC 流水线优化与仿真锚点
17. **第四章分隔页**：六场景应用闭环与后续计划
18. 六场景统一应用软件与闭环验收（`80/80` 固定输入、`112/112` 原七模型黄金帧）
19. 诚实边界与期末收尾清单（`formal_tapeout_signoff: false` 与增量上板计划）
20. 致谢（谢谢 / 欢迎提问）

## 数据来源

`main.tex` 不手写指标数字，全部来自 `make_data.py` 从仓库归档文件读取后生成的 `data/`（`data/` 被根目录 `.gitignore` 忽略，每次重新生成）：

- **11-11 ASIC 基线**：`docs/validation/eda_latest_20261111/{metrics,synthesis_area_breakdown,physical_area_breakdown}.json`、`physical_area_modules.csv`
- **统一 ASIC Lab 4 + Lab 5 签核**：`docs/validation/eda_week3_lab5_20261119/` 下的
  `metrics.json`、`synthesis_cells.json`、`synthesis_area_breakdown.json`、`physical_area_breakdown.json`、`physical_area_modules.csv`、
  `checks.json`、`build_profile.json`、`signoff_summary.json`、`sdf_result.json`、`mmmc_summary.json`、`mmmc_corners.csv`、
  `sram_interface_timing.csv`、`cdc_4corners.tsv`、`power_summary.json`、`power_workloads.csv`、`wirebond_keepouts.tsv`、
  `lab5_summary.json`、`final_erc_geometry.json`、`README.md` 与 `erc_deep_dive.md`
- **RTL 查找表重构**：`docs/validation/rtl_lookup_20261118/{provenance.json,model_cycles.csv}`、
  `hardware/npu/scripts/mutation_test.py` 与本地 `git log`（`44a76fc`、`781afc2`）
- **EGo1 ABI 5.0 8×8 FPGA 实板**：`verification/fpga/results/2026-11-11-jtag-frequency/`
  （`audit.json`、`fine/timing.csv`、`fine/timing.png`、`implementation/summary.md`）
- **主机 JTAG Single-RPC**：`docs/validation/jtag_rpc_20261118/{e2e_timing.json,ecg_1mhz_anchor.json}`（主机 RPC 时延按历史 `4.0 ms/RPC` 估计，属 RTL/Tcl 仿真）
- **六场景 Demo 软件**：`docs/validation/demo_software_20261116/summary.json`、`software/host/demos/models/{emotion,face}/training_result.json`、
  `software/host/demo.py`、`software/host/demos/signals.py`、`software/tests/test_demos.py`
- **版图与实测图（`figs/`）**：统一版图与 Lab 5 截图来自 `docs/validation/eda_week3_lab5_20261119/images/`；
  FPGA 细扫曲线来自 `verification/fpga/results/2026-11-11-jtag-frequency/fine/timing.png`

## 口径说明

- “112/112” 是 Demo 对原七模型黄金帧的比对（`existing_zoo_frames`），不是 RTL 回归数；RTL 回归为 `behavioral 85/85`、`FPGA_MEM 81/81`、`RA1SHD 81/81`。
- ABI 5.0 `8×8 @ 50 MHz` 在 EGo1 上的 4,116 次推理验证归档于 `2026-11-11`（`aa77887`）；查找表入 SRAM 与主机 Single-RPC 优化尚待合并后的增量上板复测。
- Lab 5 基于 `2000 DBU/µm` 同源 `postDummy.gds` 完成 Seal Ring（`1951.0 × 1951.0 µm <= 1980 µm`）、`CO.R.4` PIMP 修复、Dummy OD/PO+Metal 与全引擎校验；
  DRC 零阻塞违例（仅余课程认可教学类别），nmLVS 在 `3.48%` 容差下 `CORRECT`（严格 0% 仅余 45 条 IO 电阻属性差 `≤ 3.47%`），
  ERC `7 / 977` 已逐管核账且设计缺陷为 0，`formal_tapeout_signoff` 仍诚实标注 `false`。
