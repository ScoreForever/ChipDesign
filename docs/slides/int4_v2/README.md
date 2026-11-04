# INT4 无 CPU NPU 本周进展（组会幻灯片）

基于 [minimalist-pku-beamer-2026](https://github.com/rexera/minimalist-pku-beamer-2026)（MIT，见 `LICENSE.template`）。

```bash
python3 make_data.py          # 从 docs/validation/int4_v2 生成 data/（图表数据与数字宏）
latexmk -xelatex main.tex     # 生成 main.pdf
./clean.sh                    # 清理中间文件
```

- 幻灯片里的数字都来自 `data/numbers.tex` 和 `data/*.dat`，由 `make_data.py` 从仓库里的验证结果生成，不手写。
- `figs/` 是选定版图 `r90_u70_h15` 的 Innovus 截图（布局、全图、SRAM 引脚边放大）。
- 标题页的汇报人和课题组、FPGA 两页和“下一步”的 FPGA 条目是占位，等待补充。
