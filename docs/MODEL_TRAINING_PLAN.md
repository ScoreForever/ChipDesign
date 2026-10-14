# TinyCNN-8-Flat 软件模型训练与部署方案

> 本文记录当前冻结的软件基线。硬件接口、整数运算和存储布局见
> [ARCHITECTURE.md](ARCHITECTURE.md)。历史实验数据见
> [MODEL_TRAINING_RESULTS.md](MODEL_TRAINING_RESULTS.md)。修改冻结项前必须同时检查
> 训练代码、导出器、整数黄金模型、RTL 和 CPU 端软件。

## 1. 当前基线与验收标准

当前正式模型为四分类 `TinyCNN-8-Flat`：`yes/no/up/down`。Pool2 的
`5×4×8` 特征按 NHWC 顺序直接展平为 160 维，再连接 `FC(160→4)`；旧的 GAP
分类头和六分类任务只保留为历史对照，不进入当前部署包和生产 NPU 数据通路。

验收分为四层：

1. FP32 测试集报告 accuracy、macro-F1、逐类 recall 和混淆矩阵；
2. PTQ 整数模型相对 FP32 accuracy / macro-F1 下降不超过 2 个百分点；
3. Python 整数黄金模型与 RTL 对至少 100 条真实语音的原始 INT32 logits 逐位一致；
4. 软件训练目标：强增强 accuracy ≥80%，弱增强 accuracy ≥85%。

当前强增强基线达到 FP32 89.69%、INT8 89.94%；弱增强达到 FP32 90.43%、
INT8 90.56%。

## 2. 冻结规格

### 2.1 输入前处理

| 参数 | 冻结值 |
| --- | --- |
| 音频 | 单声道、16 kHz、1 秒（16000 samples） |
| 帧长 / 帧移 | 50 ms / 50 ms，即 800 / 800 samples |
| FFT / 窗 | 1024 点 / Hamming |
| 特征 | 16 维 log-mel，80–7600 Hz，log 下限 `1e-10` |
| 特征形状 | `20×16×1`，NHWC |
| 输入量化 | signed INT8 对称量化，zero point=0，固定 `input_scale` |

短音频尾部补零，长音频只保留前 1 秒。Python `software/preprocess.py` 是当前
前处理规范源；CPU C 版本必须用固定测试向量逐值对齐。

### 2.2 类别与数据划分

| 输出索引 | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| 类别 | yes | no | up | down |

- 使用 Google Speech Commands v2 官方 validation/testing 清单，保持说话人隔离；
- 四类训练样本全部保留；验证集和测试集各类等量；
- 当前四分类数量：训练 12,318、验证 1,596、测试 1,620；
- 四分类训练不使用 silence/unknown，属于闭集识别；开放麦克风的拒绝阈值尚未冻结。

### 2.3 网络

```text
Input INT8 20×16×1
 -> Conv1 3×3 SAME, 1→8 -> BN -> ReLU -> MaxPool2×2
 -> Conv2 3×3 SAME, 8→8 -> BN -> ReLU -> MaxPool2×2
 -> NHWC Flatten 5×4×8 = 160
 -> FC 160→4
 -> 4 个 raw logits
```

训练模型共有 1,324 个可训练参数。卷积本身无 bias，卷积后的 BN 在部署导出时
折叠为 INT8 卷积权重和 INT32 bias；硬件没有 BN 单元。Flatten 仅解释 Pool2
内存的线性顺序，不移动数据，也不做算术。

### 2.4 训练设置

| 项 | 冻结值 |
| --- | --- |
| Loss | CrossEntropy |
| Optimizer | Adam，初始 lr=`1e-3` |
| 调度 | 50 epoch cosine decay |
| Batch | 256 |
| Weight decay | `1e-4` |
| 最佳模型 | validation macro-F1 最大 |
| 随机种子 | 0（Python、NumPy、PyTorch、数据清单） |

正式默认使用强增强：时间平移 ±100 ms、增益 0.8–1.2、80% 概率叠加训练背景，
SNR 10–30 dB。弱增强对照为 ±50 ms、0.9–1.1、40%、SNR 15–30 dB。

## 3. 量化与导出

当前采用 PTQ：

1. 用 2048 条无增强训练样本校准输入、Conv1 和 Conv2 输出范围；
2. 激活逐张量对称 INT8；卷积和 FC 权重逐输出通道对称 INT8；
3. 卷积 bias 转为对应累加域的 INT32；
4. 每个卷积输出通道独立生成 Q0.31 multiplier 与 signed shift；
5. Pool2 保持 Conv2 scale，Flatten 不改 scale；
6. FC 输出保留 INT32 raw logits，CPU 用逐类别 `fc_cmp_mult/shift` 统一尺度后 argmax。

卷积通道 `c` 的真实倍率：

```text
real_multiplier[c] = input_scale * weight_scale[c] / output_scale
```

部署包包含前处理元数据、标签、scale、Conv1/Conv2/FC 权重与 bias、卷积
multiplier/shift、FC 比较参数，以及可直接写入 NPU 的 `256×8` INT8 权重镜像。
生产 8-lane 布局为 Conv1 base=0、Conv2 base=9、FC base=81，共用到 241 个
64-bit word。

## 4. 软硬件边界

软件负责训练、BN 折叠、量化、权重打包、log-mel、输入量化、模型装载以及最终
logit 尺度统一和分类。NPU 负责两个卷积、两个 MaxPool、NHWC Flatten 解释和 FC。
CPU 每次只发一个 start；NPU 自动完成整张网络。

## 5. 标准命令

训练强增强基线：

```powershell
D:\conda\envs\ml\python.exe software/train/train.py `
  --data-dir data/speech_commands --epochs 50 --batch-size 256
```

导出与评估：

```powershell
D:\conda\envs\ml\python.exe software/tools/export_model.py `
  --checkpoint software/train/runs/tinycnn8_4class_flatten_strong/best.pt `
  --data-dir data/speech_commands --calib-size 2048 `
  --output data/tinycnn8_flatten_strong.npz

D:\conda\envs\ml\python.exe software/tools/evaluate_quantized.py `
  --package data/tinycnn8_flatten_strong.npz --data-dir data/speech_commands
```

导出 100 条真实语音向量并做 RTL 逐位回归：

```powershell
D:\conda\envs\ml\python.exe software/tools/export_rtl_vectors.py `
  --package data/tinycnn8_flatten_strong.npz --data-dir data/speech_commands `
  --output-dir data/rtl_vectors_flatten --count 100

powershell -ExecutionPolicy Bypass `
  -File hardware/npu/scripts/run_tinycnn8_trained_test.ps1 `
  -VectorDir data/rtl_vectors_flatten
```

## 6. 尚未冻结或完成

- 开放环境中的 silence/unknown 拒绝策略与阈值；
- CPU 侧 C 语言 log-mel 与 logit 比较实现；
- 行为级 SRAM 替换、综合、时序、面积和功耗评估；
- 若未来改变类别数，必须重新训练、导出并确认 FC 权重长度和软件标签表。
