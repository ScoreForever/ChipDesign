# 证据范围

本目录在 `main` 中只保留报告、汇总 JSON 和关键证据：最终 50 MHz 实现的时序 / CDC 报告与构建输入快照
（`evidence/accepted_50mhz`、`evidence/accepted_source`）、各测试套件的 `summary.json` / `report.json`、
CDC 审查、下载日志和单元测试控制台记录，以及上板照片 `board_photo.jpg`。

逐用例的 RTL 仿真目录（`evidence/rtl*`、`zoo_supplement/rtl/<case>`、`zoo_supplement/production/<case>`）
和逐帧的实板输入输出（`zoo_supplement/board/<model>/frameXX`、`evidence/board_suite/<case>`）保留在分支
`codex/fpga-int4-v2-validation-20261104`（提交 `1339140`）。逐用例原始文件的本地链接在 main 上不可用。

2026-11-06 baseline 补入 `full_dataset_accuracy/`、独立检查/复现脚本和更新后的报告。
来源固定为 [c1f71d6](https://github.com/ScoreForever/ChipDesign/tree/c1f71d6e4f7d137e43b7740914483a08d428f820)，
它包含前述 FPGA 证据和完整数据集重评。`evidence_sha256.json` 同步取自该提交，列出全部 4,788 项；
可在该冻结提交上完整校验，main 只保留其中的关键证据与完整精度子集。
全测试集评分是软件整数参考，实板覆盖仍为 112 帧 / 224 次；原始归档分支保留。
