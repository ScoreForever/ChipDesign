# INT4 v2 验证流程

从模型到 RTL、端到端时间、ASIC 签核和报告，每一步的命令、输入和产出。
结果汇总见 `docs/INT4_V2_REPORT.md`；统一版本见 [main baseline](BASELINE_MAIN_20261106.md)。
设计结果来自 `docs/validation/int4_v2/`，独立完整测试集重评见 `verification/fpga/results/2026-11-04-int4-v2/full_dataset_accuracy/`。

验证分四层，前两层只依赖仓库内容：

| 层 | 内容 | 需要 |
|---|---|---|
| 1 | RTL 程序回归 | Icarus Verilog |
| 2 | 发布模型逐位核对：参考模型、RTL、端到端 JTAG | Python、torch、Icarus |
| 3 | 完整测试集精度复现 | 公开数据集（脚本下载） |
| 4 | 重新训练 | GPU、数据集（H100 上每个模型 2–15 min） |
| ASIC | 综合、全芯片 PnR、签核、SDF 门级仿真 | 课程 EDA 节点与 PDK |

## 仓库里有什么

| 路径 | 内容 |
|---|---|
| `software/zoo/pretrained/<model>/model.json` | 导出的 INT4 模型（权重、逐通道参数），即上板/RTL 用的模型 |
| `.../qat_state.pt` | QAT checkpoint（浮点权重和学到的量化尺度），可继续微调或重新导出 |
| `.../c8/`、`.../c4/` | 编译好的 NPU 镜像 `model.bin` + `manifest.json`（4×8 / 4×4 阵列，全部融合） |
| `.../golden.json` | 16 帧测试样本（在测试集上等间隔取）：输入码、标签、整数参考输出，以及主机写入/读出的原始字节（RTL 回放、上板测试都用它） |
| `.../training_result.json`、`train.log` | 训练时的指标和 `train.py` 的完整参数 |
| `software/zoo/pretrained/MANIFEST.json` | 每个文件的 SHA-256、完整测试集 INT4 精度、训练参数 |
| `docs/validation/int4_v2/*.json` | 所有验证结果（精度、RTL 周期与拆解、端到端、面积、布局、签核） |

## 仓库里没有什么（以及原因）

| 内容 | 原因 | 在哪里 |
|---|---|---|
| 数据集、`test_frames.pt` | 体积大（7 个模型共 76 MB）、各数据集有自己的分发许可；可由脚本重新生成并逐位校验 | `software/zoo/fetch_datasets.sh` + `make_test_frames.py` |
| GDS、DEF、LEF、SPEF、网表、SDF | 含 TSMC 0.18 µm PDK 与 SRAM 宏的授权数据，**不能进公开仓库** | 课程 EDA 节点的私有归档；当前候选名 `diagonal_facing_h25`，按交付记录定位 |
| EDA 原始日志 | 体积大；关键数字已整理进 `docs/validation/int4_v2/` 的 JSON | 同上 |

## 环境

- Python 3.12；`pip install -r software/zoo/requirements.txt`（训练/评估用 GPU 版 torch 时按 CUDA 版本另装）
- Icarus Verilog 12（`iverilog`/`vvp`）；FPGA 余量估算另需 yosys
- ASIC：课程 EDA 节点，`source /opt/eda/env.sh; source /lec/eda.env`

## 第 1 层：RTL 程序回归

```bash
python3 hardware/npu/scripts/run_program_regression.py --jobs 32
python3 hardware/npu/scripts/run_program_regression.py --jobs 32 --define CHIPDESIGN_FPGA_MEM --output build/regression_fpga_mem
python3 hardware/npu/scripts/run_program_regression.py --jobs 32 --define NPU_TILE=8 --output build/regression_tile8
python3 -m unittest discover -s hardware/asic/optimize -p 'test_*.py'
```

32 个 fixture × 融合开/关 × 4×8、4×4、4×8 JTAG，逐条核对每个张量写回；另有 16 个非法描述符、
20000 个 Requant 向量和 INT4/u4 MAC 穷举。

## 第 2 层：发布模型逐位核对（不需要数据集）

```bash
# 当前编译器和整数参考是否还能逐字节复现发布的镜像与 golden 输出
python3 software/zoo/check_golden.py

# RTL：golden 帧在 RTL 上开/关融合逐位核对，并统计周期与瓶颈
python3 software/zoo/rtl_check.py --runs software/zoo/pretrained --output build/zoo_rtl --frames 3 --jobs 32

# 端到端：物理 JTAG 管脚、TCK 1 MHz，突发与逐字两种协议的分阶段时间
python3 software/zoo/e2e_timing.py --runs software/zoo/pretrained \
    --models mnist_l,fashion_cnn,kws_l,kws_m,har,ecg,gtsrb_l --output build/e2e
```

`rtl_check.py` 的结果对应 `docs/validation/int4_v2/zoo_rtl_final.json`，`e2e_timing.py` 对应 `e2e.json`。
2026-11-04 在 PKUh100 上直接用发布文件运行：32 个可放下的配置全部逐位一致（其余 24 个配置超出描述符或
SRAM 限制，与报告一致），第 0 帧周期数与 `zoo_rtl_final.json` 25/25 相同；ECG 端到端 TCK 数与 `e2e.json` 相同。

## 第 3 层：完整测试集精度

已归档的七个完整任务测试集共 67,245 条评分，可在没有数据集和 PyTorch 的环境独立复核：

```bash
python3 verification/fpga/evidence/check_int4_v2_full_accuracy.py
```

这是软件整数参考评分，不是完整数据集逐帧 FPGA 执行。输入锚点、逐类指标、预测 CSV 和环境条件见
[完整精度报告](../verification/fpga/results/2026-11-04-int4-v2/full_dataset_accuracy/report.md)。
重新下载/重建时须对齐该报告的前端版本，特别是 GTSRB 的 Pillow 13.0.0；不能跳过 golden 输入检查。

```bash
PYTHON=python3 software/zoo/fetch_datasets.sh datasets
python3 software/zoo/make_test_frames.py --data datasets --models software/zoo/pretrained
python3 software/zoo/evaluate.py --runs software/zoo/pretrained --output build/zoo_eval.json
```

`make_test_frames.py` 会先确认重建的帧与 `golden.json` 逐位一致（数据集版本或预处理不一致会直接报错）；
GTSRB 的 parquet 文件按 SHA-256 校验。`evaluate.py` 的结果应与 `docs/validation/int4_v2/zoo_eval.json`
完全相同（2026-11-04 在 PKUh100 上从发布文件 + 原始数据集复现，7 个模型逐个相同）。

| 模型 | 数据集 | INT4 测试精度 |
|---|---|---:|
| mnist_l | MNIST | 98.88% |
| fashion_cnn | Fashion-MNIST | 90.11% |
| kws_l | Speech Commands v2（12 类） | 91.51% |
| kws_m | Speech Commands v2（12 类） | 90.43% |
| har | UCI-HAR 原始 9 轴 | 92.74% |
| ecg | MIT-BIH AAMI 5 类（病人内划分） | 97.01% |
| gtsrb_l | GTSRB 43 类 | 92.02% |

## 第 4 层：重新训练

参数取自各模型 `training_result.json` 的 `args`：

```bash
python3 software/zoo/train.py mnist_l     --data datasets --out runs --fp-epochs 80  --qat-epochs 60  --seed 0 --distill
python3 software/zoo/train.py fashion_cnn --data datasets --out runs --fp-epochs 200 --qat-epochs 200 --seed 1 --distill --affine
python3 software/zoo/train.py kws_l       --data datasets --out runs --fp-epochs 150 --qat-epochs 150 --seed 2 --distill
python3 software/zoo/train.py kws_m       --data datasets --out runs --fp-epochs 80  --qat-epochs 60  --seed 0 --distill
python3 software/zoo/train.py har         --data datasets --out runs --fp-epochs 80  --qat-epochs 60  --seed 0 --distill
python3 software/zoo/train.py ecg         --data datasets --out runs --fp-epochs 80  --qat-epochs 60  --seed 0 --distill --balanced
python3 software/zoo/train.py gtsrb_l     --data datasets --out runs --fp-epochs 150 --qat-epochs 150 --seed 1 --distill --affine
```

GPU 非确定性会让重新训练的结果有小幅差异；发布的 `model.json` 是报告数字的依据。
部分模型训练早于参数格式定稿（Q8 乘数、u4），`training_result.json` 里的精度是训练时的旧语义，
以 `evaluate.py` 在当前 ABI 下的重新评分为准。新训练结果可用
`python3 software/zoo/package_models.py --runs runs --output software/zoo/pretrained` 打包。

## ASIC

在课程 EDA 节点、冻结的源码副本中运行（细节见 `hardware/asic/optimize/README.md`）：

```bash
source /opt/eda/env.sh; source /lec/eda.env
E=/absolute/new/experiment
python3 hardware/asic/optimize/flow.py preflight --output $E
python3 hardware/asic/optimize/flow.py trial --output $E --name diagonal_facing_h25 \
    --layout diagonal_facing --utilization 0.70 --congestion --hold-target 0.25
python3 hardware/asic/optimize/postlayout.py --candidate $E/candidates/diagonal_facing_h25   # Xcelium SDF 门级仿真
python3 hardware/asic/optimize/signoff.py   --candidate $E/candidates/diagonal_facing_h25   # Calibre DRC/LVS、PrimeTime、Formality
```

选定版图的结果在 `docs/validation/int4_v2/layout_final.json` 和 `signoff.json`：1920 µm die，
TT corner 的 PrimeTime setup +1.094 ns / hold +0.154 ns。当前候选 hold target 为 0.25 ns；
旧 `r90_u70_h15` / 1860 µm 是过程实验，不是 main baseline。多角 STA、IR/EM 等未覆盖项保留。

## 报告与图表

```bash
python3 docs/tools/int4_v2_report.py          # docs/INT4_V2_REPORT.md 与 int4_v2_summary.json
python3 docs/tools/int4_v2_dashboard_data.py  # 交互网页的数据表
python3 docs/slides/int4_v2/make_data.py      # 组会幻灯片的图表数据
```

`int4_v2_report.py` 计算 MAC 利用率时会读取 `zoo_eval.json` 里记录的训练目录；
在没有这些目录的机器上，可把 `run` 字段指向 `software/zoo/pretrained/<model>`。
