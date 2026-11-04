# 证据范围

本目录在 `main` 中只保留报告、汇总 JSON 和关键证据：最终 50 MHz 实现的时序 / CDC 报告与构建输入快照
（`evidence/accepted_50mhz`、`evidence/accepted_source`）、各测试套件的 `summary.json` / `report.json`、
CDC 审查、下载日志和单元测试控制台记录，以及上板照片 `board_photo.jpg`。

逐用例的 RTL 仿真目录（`evidence/rtl*`、`zoo_supplement/rtl/<case>`、`zoo_supplement/production/<case>`）
和逐帧的实板输入输出（`zoo_supplement/board/<model>/frameXX`、`evidence/board_suite/<case>`）保留在分支
`codex/fpga-int4-v2-validation-20261104`（提交 `1339140`）。`evidence_sha256.json` 列出全部证据文件的
SHA-256，可在该分支上逐个核对；报告中指向这些文件的链接在本分支上不可用。
