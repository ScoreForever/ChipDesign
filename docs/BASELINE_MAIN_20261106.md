# main baseline：INT4 v2 / ABI 4.0

审查日期：2026-11-06（Asia/Singapore）。`main` 为后续开发唯一基线，冻结标记为
`baseline-int4-v2-20261106`。新功能从最新 main 建分支，经验证后合回；历史实验分支不再作为默认开发入口。

## 版本来源与整合方式

- 起点：远程 main `e6efb37373454aaaffd5abd4571fed071ef5874b`，已含 PR #13/#14。
- 保留 main 的 RTL、主机编译器、七个发布模型、FPGA 修复及最终 ASIC `diagonal_facing_h25` 布局记录。
- 补入 `c1f71d6e4f7d137e43b7740914483a08d428f820` 的完整测试集精度证据、三个检查/复现脚本及相应报告更新。
- FPGA 原始归档固定在 `1339140a764d4f973bc7bf4cbcc6f31a7ee7a2a7`。
- 不整体合并旧验证分支：main 已采用其 RTL 修复和关键证据，整支合并会重复引入数千个逐用例归档。
- 不覆盖 main 较新的 ASIC 布局、报告和幻灯片；不删除或强推历史分支。

整合前逐目录比较确认：main 与 `c1f71d6` 的 `hardware/npu/`、`hardware/soc/rtl/`、
`hardware/fpga/`、`software/host/`、`software/zoo/` 无差异。ASIC 流程的差异是 main 新增的
spread macro layouts；已保留并重新运行流程单测。独立验证分支的 4,788 个证据摘要全部匹配，
已实现 FPGA 的 provenance 源文件摘要与验证分支一致，故无需借合并之名重新修改硬件。

## 审查发现与修正

| 问题 | 影响 | 本次处理 |
|---|---|---|
| README、架构说明和软件入口仍写 ABI 3.0、五算子和旧参数语义 | 容易构建错误模型或误读硬件能力 | 对齐 ABI 4.0、七算子、i4/u4、压缩参数、单次舍入和生产 trace 默认值 |
| 完整数据集独立重评未纳入 main，验证矩阵仍为 NOT RUN | 最新结论散落在验证分支 | 导入原始 CSV、报告、环境/来源摘要和三个复现脚本，更新报告及矩阵 |
| ASIC 复现文档仍指向 r90_u70_h15 / 1860 µm | 复现的布局不是最终版本 | 对齐 diagonal_facing_h25 / 1920 µm、hold target 0.25 ns |
| CI 未触发 zoo / FPGA 验证内容，也不检查发布模型和完整精度记录 | 更新可能绕过验证 | 扩展路径过滤，增加标准库模型重编译/golden 与 CSV 指标检查 |

本轮是分支整合、契约/证据一致性审查及可复跑回归，不是所有 RTL 的逐行形式证明或重新流片签核。

## 远程分支处置表

快照基于同步后的 26 个远程分支。已纳入表示 tip 是整合前 main 的祖先；
“等价内容”表示内容已转入但 Git ancestry 不相同，不能只靠 ahead/behind 数字决定合并。
本次保留所有原分支，下面的归档是用途定义，不是远程删除操作。

| 分支 | tip | 定位 / 处置 |
|---|---|---|
| main | e6efb37 | 本次 baseline 起点 |
| feature/int4-v2 | ebca2ce | 已纳入 main；后续开发从 main 起步 |
| no_cpu_INT4 | bf3b4ab | PR #12 的旧整合点；功能已纳入 main，保留历史 |
| codex/fpga-int4-v2-validation-20261104 | 1339140 | RTL 修复/关键证据已转入 main；保留完整原始 FPGA 归档 |
| codex/int4-v2-full-dataset-accuracy-20261104 | c1f71d6 | 本次提取完整精度证据；保留完整可校验归档 |
| codex/fpga-int4-board-validation-20261101 | 4d432e5 | 已纳入；旧 ABI 3.0 板测历史 |
| codex/fpga-cdc-timing-review-20261030 | 346808f | 已纳入；历史时序审查 |
| codex/npu-fpga-timing-fix-20261031 | 88cae88 | 已纳入；历史时序修复 |
| codex/nocpu-fpga-board-suite | a6dbec9 | 已纳入；历史验证基础设施 |
| codex/fpga-nocpu-h16-validation | 505c9e4 | 旧 noCPU/H16 板测；不移植为 v2 证据 |
| codex/fpga-nocpu-validation-20261102 | 412c863 | ABI 2.1 的物理复位/断电证据；不移植为 v2 证据 |
| exp/no_cpu | 175484e | INT8 实验、旧动画；CDC 约束已有后续移植，保留历史 |
| exp/smaller_cpu_PicoRV32 | f80a45b | CPU 替换实验，与当前 noCPU 方向不同 |
| eda-results-2026-10-19 | 9e19386 | 旧源码/版图证据归档 |
| eda-results-main-align-20261021 | 8c359a1 | 旧 main 对齐实验归档 |
| eda-results-int8-int4-20261102 | eefaed6 | 旧 INT8/INT4 对照归档，不覆盖最终 v2 布局 |
| codex/fused-speech-events | 983067b | 已纳入；历史融合工作 |
| codex/main-npu-area | 28d1616 | 已纳入；历史面积优化 |
| feature/asic-pnr-optimization | 5771389 | 已纳入；历史 ASIC 流程 |
| feature/baseline | 4c769b7 | 已纳入；旧 CPU 软件基线，不是当前 baseline |
| feature/matrix-unit | 6edf3d8 | 已纳入；早期模块实现 |
| feature/vector-unit | 06d1d9d | 已纳入；早期模块实现 |
| feature/npu-fused-flat | 5d1ce2c | 已纳入；旧固定模型实现 |
| feature/tinycnn8-npu | e663a41 | 已纳入；旧固定模型实现 |
| feature/tinycnn8-tiled-profiler | b910290 | 已纳入；旧 tiled/profiler 实验 |
| copilot/create-repository-directory-structure | aba8ce9 | 已纳入；初始目录结构 |

## 验证结果和边界

本次本地复核：

- 相同硬件/主机源码的完整程序回归 130/130，含 16 种非法描述符、生命周期和块级数值/存储检查。
- trace 配置 8/8；INT4 软件单测 7/7；七个发布模型的 c4/c8 镜像重编译及 112 帧 golden 检查通过。
- 最终 main ASIC 流程单测在 WSL/Linux 为 37/37。原生 Windows 不支持该流程的 POSIX 进程组接口，不能用其失败判定 ASIC 实现失败。
- FPGA 工具单测在 Windows 为 22 项、21 通过/1 跳过；WSL 缺 tkinter，故该套件采用具备 Tcl/Tk 的 Windows 环境验证。
- 完整测试集 CSV 独立重建混淆矩阵、准确率、平衡准确率和逐类召回率：7 个模型、67,245 条评分全部匹配发布结果。
- 全量归档的 4,788 个 SHA-256 匹配；main 有意只保留关键证据和完整精度子集，见 [证据范围](../verification/fpga/results/2026-11-04-int4-v2/EVIDENCE.md)。

历史签核证据保留，未在本轮重新运行 Vivado、烧录、数据集 GPU 推理或课程 EDA：

| 层 | 已有证据 | 未覆盖 / 限制 |
|---|---|---|
| FPGA | 50 MHz、4×8、trace-off；七模型 112 帧、224 次冷/热推理逐字节一致 | setup WNS 0.000 ns、hold +0.035 ns；物理按键、断电恢复、电气波形、Flash 启动仍未完成 |
| 完整精度 | 软件整数参考 67,245 次模型样本评分，与发布指标一致 | 不是完整测试集逐帧 FPGA 执行；ECG 为病人内划分，KWS 两模型共享同一测试集 |
| ASIC | diagonal_facing_h25；1920 µm；TT setup +1.094 ns / hold +0.154 ns；记录 DRC/LVS/LEC/SDF 通过 | 仅 TT；DRC/LVS 有已接受的 IO guideline/property 说明；无多角 STA、IR/EM、seal ring 或芯片级 ESD review |

复现入口见 [VALIDATION_FLOW.md](VALIDATION_FLOW.md)。后续改变 RTL、约束、trace 或工具配置时，
必须重新绑定对应实现的源码摘要，不能直接沿用这一版的板级时序或 ASIC 签核数字。
