# Week 3 全栈进展（11-11 → 11-18，组会幻灯片，14 页）

接 [`../abi5`](../abi5)。覆盖 `main` 上已合入的 PR #17（六场景 Demo）、PR #18（主机 JTAG Single-RPC）、
PR #15/#19（`npu_tensor_lookup` 移入激活 SRAM 及其对齐基线 RTL 复核）、PR #20（Week 3 ASIC：TT 50 MHz、
四引擎签核、4-Corner MMMC、窗口化 VCD 功耗）。
模板为 [minimalist-pku-beamer-2026](https://github.com/rexera/minimalist-pku-beamer-2026)（MIT，见 `LICENSE.template`），
沿用小组改动：页脚为课程名、去掉顶部章节导航；不生成章节分隔页（`\AtBeginSection[]{}`），每个 frame 对应一页。

```bash
python3 make_data.py          # 生成 data/（数字宏 numbers.tex 与表格 / 图表片段）
latexmk -xelatex main.tex     # 生成 main.pdf（14 页）
python3 make_pptx.py          # 生成 main.pptx（每页一张图，文字放在备注；需 pymupdf、python-pptx）
./clean.sh                    # 清理中间文件
```

## 页面结构

1 标题 · 2 全栈总览 · 3 JTAG Single-RPC 与 Demo 修复 · 4 查找表移入 SRAM 架构 · 5 RTL 验证与故障注入 ·
6 综合与层次面积对比 11-11 · 7 物理实现与版图 · 8 四引擎签核与 SDF · 9 MMMC / SRAM 接口 / CDC ·
10 VCD 功耗与融合节能 · 11 六场景 Demo · 12 课程 Lab 对照与 AI 红队 · 13 诚实边界与下一步 · 14 Q&A

## 数据来源

`main.tex` 不手写指标数字，全部来自 `make_data.py` 生成的 `data/`（`data/` 被根目录 `.gitignore` 忽略，每次重新生成）：

- 11-11 基线：`docs/validation/eda_latest_20261111/{metrics,synthesis_area_breakdown,physical_area_breakdown}.json`、`physical_area_modules.csv`
- PR #20：`docs/validation/eda_week3_20261118/` 下的 `metrics.json`、`synthesis_cells.json`、`synthesis_area_breakdown.json`、
  `physical_area_breakdown.json`、`physical_area_modules.csv`、`checks.json`、`experiment.json`、`build_profile.json`、
  `signoff_summary.json`、`sdf_result.json`、`mmmc_summary.json`、`mmmc_corners.csv`、`sram_interface_timing.csv`、
  `cdc_4corners.tsv`、`power_summary.json`、`power_workloads.csv`；`README.md` 中解析候选名、利用率、hold 目标、
  核心时钟、`SS125_DERATED` 周期与 LVS IO 库版本 / 课程容差（JSON 中没有这些字段）
- PR #19：`docs/validation/rtl_lookup_20261118/{provenance.json,model_cycles.csv}`
- PR #15：回归与故障注入计数只记录在提交说明里，从本地 `git log` 读取 `44a76fc`、`781afc2`（`main` 历史，不需要 fetch）；
  新增故障注入列表来自 `hardware/npu/scripts/mutation_test.py`
- PR #18：`docs/validation/jtag_rpc_20261118/{e2e_timing.json,ecg_1mhz_anchor.json}`（RPC 时延为 4.0 ms/RPC 历史估计）
- PR #17：`docs/validation/demo_software_20261116/summary.json`、`software/host/demos/models/{emotion,face}/training_result.json`、
  `software/host/demo.py`（场景数）、`software/host/demos/signals.py`（HAR 缺口阈值）、`software/tests/test_demos.py`（三项修复的回归测试）
- 版图：`figs/` 中的 PNG 复制自 `docs/validation/eda_week3_20261118/images/`
- 第 12 页的多模型评审流程（Opus / Sol / Astra / 人工 gate）是会话级工作流约定，原始对话未归档进仓库；仓库内可追溯的是 PR #18–#21 的提交与审查记录
- 第 12 页的课程 Lab 对照依据课程官网核对记录（仓库外）；Lab 4 die 上限 1950 µm 作为常量写在 `make_data.py`

## 口径说明

- “112/112” 是 Demo 对原七模型黄金帧的比对（`existing_zoo_frames`），不是 RTL 回归数；RTL 回归为
  behavioral 85/85、`FPGA_MEM` 81/81、`RA1SHD` 81/81（PR #15）。
- Demo 三项评审修复在提交 `1759302`，随 PR #17 合入，不属于 PR #18。
- DRC / LVS 为课程政策下接受：原始 DRC 2,427 条（IO 单元 + `DRM.R.1`），LVS 原始 `INCORRECT`（45 条 IO 电阻偏差 ≤ 3.47%）。
