# INT4 v2 完整测试集精度重评

2026-11-04（Asia/Singapore）。模型发布提交 `78664e9`，FPGA 验证基线 `1339140`。
交付分支：`codex/int4-v2-full-dataset-accuracy-20261104`。

## 结论与范围

**7 个冻结 INT4 模型的完整任务测试集精度重评全部通过。** 共完成 **67,245 次模型样本评分**，准确率、平衡准确率和每个类别召回率均与 `docs/validation/int4_v2/zoo_eval.json` **逐值一致**。7×16=112 个发布样本的重建输入、标签和整数输出也全部匹配。

本轮使用 GPU 上的 `npu_fastref` 执行当前 ABI 4.0 的整数模型，并未在 FPGA 上逐帧运行完整测试集。FPGA 层已有同一模型包的 112 帧/224 次实板逐位一致验证，见 [FPGA 模型补测](../zoo_supplement/report.md)。本轮补齐的是完整测试集的软件整数精度，不能写成 67,245 次完整测试集实板推理。

没有重新训练、没有调整模型或量化参数、没有用测试集重新选择 checkpoint。FPGA 原验证分支仍固定在 `1339140`；本报告在独立分支交付。

## 实测结果

平衡准确率为每类召回率的宏平均（`math.fsum(recalls)/class_count`）。末列是准确率相对发布结果的百分点差，全部为零。未在本轮重评 FP32 精度。

| 模型 | 完整测试帧数 | 正确帧数 | INT4 准确率 | 平衡准确率 | 相对发布差（百分点） |
|---|---:|---:|---:|---:|---:|
| mnist_l | 10,000 | 9,888 | 98.8800% | 98.8612% | 0 |
| fashion_cnn | 10,000 | 9,011 | 90.1100% | 90.1100% | 0 |
| kws_l | 4,888 | 4,473 | 91.5098% | 91.4909% | 0 |
| kws_m | 4,888 | 4,420 | 90.4255% | 90.3982% | 0 |
| har | 2,947 | 2,733 | 92.7384% | 92.8758% | 0 |
| ecg | 21,892 | 21,237 | 97.0080% | 81.8466% | 0 |
| gtsrb_l | 12,630 | 11,622 | 92.0190% | 88.7856% | 0 |

两种 KWS 模型评估同一 4,888 帧任务测试集，因此跨模型评分数为 67,245，去除这部分重复任务样本后为 62,357；不计算跨不同任务的混合准确率。

逐类召回率、类别数量和混淆矩阵见各模型的 `<model>/report.json`；完整逐样本预测见 `<model>/predictions.csv.gz`，列为 test_index、label、prediction、correct。混淆矩阵的行是真值类别，列是预测类别。逐样本 CSV 重新生成混淆矩阵、正确数和召回率后均与报告一致。

## 测试集定义和重建

- **MNIST / Fashion-MNIST**：各官方 10,000 帧测试集；像素 `(pixel/255−0.5)/0.5`，再按冻结模型 input_scale 量化。
- **HAR**：UCI HAR 官方 subject-disjoint 测试划分，9 轴原始 128 点信号；使用训练数据的逐轴 mean/std，测试集 2,947 帧。
- **ECG**：MIT-BIH 1.0.0 的 48 个记录，AAMI N/S/V/F/Q 五类，256 点窗口降采样到 128 点、逐拍 z-score；按发布 loader 的 seed=0、70/10/20 病人内逐拍分层划分，测试 21,892 帧。这不是 inter-patient DS1/DS2 协议。
- **KWS-L / KWS-M**：Speech Commands v2，官方 testing_list；十个关键词 + unknown + silence 的 12 类任务，按发布 loader 固定 seed、unknown 子采样与背景噪声 silence 构造，任务测试集各 4,888 帧。完整评估此任务测试集，不是将整个 35 词原始语音库直接当作 12 类测试集。MFCC 为 49×10，使用训练特征的全局 mean/std。
- **GTSRB**：发布指定的 parquet 副本，官方 12,630 张测试图，RGB bilinear 到 32×32、逐图 z-score/2。train/test parquet 的 SHA-256 与发布 fetch 脚本要求一致。

所有模型均由原 `data.py` loader 重建完整测试划分，使用原 `train.quantize_input` 与 `hwopt.frames_for` 量化/补通道。检查完整帧数、16 个间隔锚点的输入与标签，以及其打包字节；GPU fastref 的全部锚点输出再次与已由 scalar reference/RTL/FPGA 验证的 golden 比较。

原始下载来源和文件字节数、SHA-256 见 [dataset_provenance.json](dataset_provenance.json)：

| 数据 | 本轮获取方式 |
|---|---|
| Speech Commands v2 | Google 官方 `storage.googleapis.com/download.tensorflow.org/data/speech_commands_v0.02.tar.gz` |
| UCI-HAR | Coursera 课程的 UCI HAR zip 副本，CloudFront；ZIP CRC 完整，全部输入/标签锚点和最终逐类指标与发布一致 |
| MIT-BIH | PhysioNet 官方 1.0.0 ZIP；解压后核验 48 个记录头文件 |
| MNIST | 复用本机 torchvision 原始 IDX；gz 文件 MD5 与 torchvision 官方资源表一致 |
| Fashion-MNIST | 官方 `fashion-mnist.s3.eu-central-1.amazonaws.com`，四个 gz 均按 torchvision 官方 MD5 校验 |
| GTSRB | HF mirror 的 `tanganke/gtsrb` parquet，按发布预期 SHA-256 严格校验 |

下载器采用精确 HTTP Range 分块、Content-Range 和块长度核对、失败续传；完成后按序拼接，并用 ZIP CRC/gzip 解压及适用的预期摘要检查完整性。逐文件 SHA-256 和实际用于评分的完整 frames/labels 值摘要均保存，避免只记录下载 URL。

## 环境差异与已解决的问题

本轮运行于 Windows、RTX 4060 Laptop GPU，PyTorch **2.5.1+cu121**、torchvision/torchaudio **0.20.1/2.5.1+cu121**、NumPy **2.4.3**、wfdb **4.3.1**、soundfile **0.14.0**。各次执行的具体版本记录在各模型 report.json。环境通过独立 venv 复用既有 CUDA PyTorch，没有修改全局环境。

**GTSRB 的图像库版本是实际复现条件。** 最初 Pillow 12.1.1 重建的第 7893 帧有 45 个量化元素与发布 golden 不同，脚本在评分前拒绝通过；见 [失败证据](environment_mismatch/pillow12_failed_report.json)。升级隔离环境到发布使用的 **Pillow 13.0.0** 并重新生成 gtsrb.pt 后，全部 16 个锚点匹配，完整测试集 11,622/12,630 以及各类召回率均与发布结果相同。没有跳过锚点检查或用容差放行不同的输入。

MNIST/HAR 在 Pillow 升级前已完成，它们的 IDX/惯性信号前端不经过图像 resize。GTSRB、Fashion、ECG、KWS 的最终报告记录 Pillow 13.0.0。不要跨图像库版本复用已缓存的 gtsrb.pt。

Python 3.11 与发布 Python 3.12 的内置 sum 对宏平均可能出现约 3×10⁻¹⁶ 的累加舍入差。本轮用 math.fsum 计算每类召回率均值；未改变任何分类结果、类别计数或舍入后的整数输出，最终发布指标也逐值相同。

## 证据、复核与限制

[audit.json](audit.json) 汇总全部 7 个模型、样本数量、准确率、召回率、frames/labels/logits SHA、GPU 评分耗时和显存峰值。63 个发布模型文件再次按 Git blob SHA-256 核验，14 个 c4/c8 镜像重编译后与发布镜像逐字节相同。

逐模型本地 `test_frames.pt` 保存于 `build/int4_v2_full_accuracy_20261104/evaluation/<model>/`，原始数据和缓存保存在同一 build 目录的 datasets/。本次 Git 提交只包含代码、报告、预测/标签 CSV、摘要和日志，不上传原始图像、语音、ECG、惯性信号或完整输入帧缓存。每份压缩 CSV 和所有报告纳入父目录 evidence_sha256.json，并核对提交后的 Git blob。

GPU fastref 使用 float64 MAC/pool 累加和 int64 量化来模拟固定整数语义；112 个已知输出锚点经过逐元素核对。该软件重评不是所有输入上的形式等价证明，也不替代完整测试集的 FPGA 逐帧执行。没有统计置信区间、重新划分测试集或跨任务平均精度保证。

本轮没有改变 RTL、时钟约束或 bitstream，因此前轮 **50 MHz WNS 0.000 ns / WHS +0.035 ns** 仍是相同时序结果，setup 裕量限制保留。物理按键、断电恢复、电气波形和 Flash 自动启动仍待后续，不能因软件精度重评通过而标记完成。

## 复现

使用隔离环境，Pillow 对齐 13.0.0；安装 torchvision、torchaudio、wfdb、soundfile、pandas、pyarrow 等既有依赖。每次 output 用新目录，避免将之前的缓存误作新重建结果：

```powershell
python verification/fpga/fetch_int4_v2_ranges.py --data build/accuracy_recheck/datasets
```

本次复用了已存在的官方 MNIST 原始 IDX；新的空目录另需用 torchvision 下载 MNIST 的 train/test 到 `<data>/tv`（完整 loader 使用训练拆分，但测试精度只评分官方 test）：

```python
from torchvision.datasets import MNIST
for train in (True, False):
    MNIST('build/accuracy_recheck/datasets/tv', train=train, download=True)
```

数据全部就绪后执行：

```powershell
python verification/fpga/reevaluate_int4_v2_zoo.py --data build/accuracy_recheck/datasets --output build/accuracy_recheck/evaluation --batch-size 256
```

无需 PyTorch 即可独立复核已归档的逐样本 CSV：

```powershell
python verification/fpga/check_int4_v2_full_accuracy.py
```

本轮依赖版本和设备信息另见 [environment.json](environment.json)。

下载脚本不会把未核对的整文件 Range 响应当成一个分块；若某个源不支持正确的 Range 或文件摘要变化，应保留失败并复核数据来源。若复现时 Pillow/前端不同而锚点不匹配，应重建并对齐环境，不能删除检查后继续宣称相同精度。
