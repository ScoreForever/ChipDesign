# 先进芯片全栈设计与协同优化：答辩演示文稿

19 页中英混排 Beamer 演示文稿（16:9，ISSCC/VLSI 极简风），附 PPTX 与演讲者备注。

## 构建

```bash
export PATH="$HOME/.local/bin:$HOME/.local/share/mamba/envs/chip/bin:$PATH"
python3 make_data.py                                 # 从验证档案生成 data/ 与 figs/
latexmk -xelatex -interaction=nonstopmode main.tex   # 编译 main.pdf
python3 make_pptx.py                                 # 导出 main.pptx（备注取自 speaker_notes.md）
./clean.sh                                           # 清理 TeX 中间文件，保留 main.pdf 与 main.pptx
```

`make_data.py` 只用 Python 标准库；`make_pptx.py` 需要 `pymupdf` 与 `python-pptx`。

## 字体

全部为 TrueType，通过字体名自动发现：Microsoft YaHei、Segoe UI、Consolas（已安装于系统/用户字体目录时优先使用）；找不到时依次回退到 PingFang SC / Noto Sans CJK SC、Helvetica Neue / Liberation Sans、Menlo / Liberation Mono。

## 数据来源

`main.tex` 不含硬编码指标，全部数字来自 `data/numbers.tex` 宏和 `data/*.dat`、`data/*.tex` 表格，由 `make_data.py` 读取：

- ASIC 签核：`docs/validation/eda_final_coopt_1980_20261121/`（`metrics.json`、`synth_area.json`、`area_breakdown.json`、`final_cells.json`、`mmmc_corners.csv`、`sram_interface_timing.csv`、`rc_sensitivity.tsv`、`signoff_summary.json`、`dual_sdf_summary.json`、`lab5_summary.json`、`final_erc_geometry.json`、`power_summary.json`、`delivery_manifest.json`、`images/`）
- FPGA 实板：`docs/validation/fpga_final_live_20261121/`（`coopt_summary.json`、`models/`、`scenes/`、`resnet20/`、`resnet18/`、`rtl_audit/`）
- 源码参数：`hardware/npu/rtl/npu_program_core.sv`、`hardware/npu/rtl/npu_mac_array.sv`、`hardware/soc/rtl/control/npu_jtag_transport.sv`、`software/host/nocpu_jtag.py`、`software/host/npu_program.py`、`software/compiler/tiler.py`；FIFO 修复前的读块默认值经 `git show <fix>^:software/host/nocpu_jtag.py` 读取

`data/`、`figs/`、`main.pdf`、`main.pptx` 均可重新生成。
