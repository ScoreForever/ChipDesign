# 保持RTL兼容的模型准确率实验（2026-10-14）

目标是提高关键词识别准确率，保持TinyCNN-8-Flat的输入、两层8通道卷积、两个MaxPool、
NHWC Flatten、160→4 FC和INT8部署ABI。没有改变RTL或增加模型参数；训练参数量仍为1,324。

## 1. 协议与选择

agi上未找到此前模型checkpoint，本次重新训练strong基线，并比较weak与weak+0.05
label smoothing。每组50epochs、batch256、Adam lr=1e-3、cosine衰减、weight decay=1e-4；
num_workers=4。首轮seed0，扩展轮增加候选seed1/2，共1个基线与6个候选。
label smoothing只影响训练损失，推理结构不变。

每个训练run按FP32 validation macro-F1保存best checkpoint，并延迟test评估。
候选全部经2,048条无增强训练样本做PTQ，再在固定seed0的1,400条INT8验证集上比较。
最终候选按INT8 validation macro-F1、accuracy顺序选择；test不用于选择候选。
扩展轮复用首轮seed0 checkpoint，重新导出/评估；两轮均选中`candidate_weak_smooth`。
训练seed也影响增强与checkpoint验证子集；最终候选比较与完整test均固定evaluation seed0。

## 2. 验证集与完整测试结果

| INT8模型 | Validation accuracy | Validation macro-F1 |
| --- | ---: | ---: |
| strong seed0基线 | 90.50% | 90.52% |
| weak seed0 | 90.71% | 90.75% |
| weak+0.05 smoothing seed0 | **91.43%** | **91.43%** |
| weak seed1 | 90.79% | 90.81% |
| weak+0.05 smoothing seed1 | 90.64% | 90.66% |
| weak seed2 | 90.07% | 90.11% |
| weak+0.05 smoothing seed2 | 90.71% | 90.73% |

仅基线和选中候选在1,620条test语音（每类405条）上评估：

| INT8模型 | Test accuracy | Test macro-F1 |
| --- | ---: | ---: |
| 本次重训strong基线 | 89.57% | 89.60% |
| 选中weak+0.05 smoothing seed0 | **90.06%** | **90.12%** |
| 差值 | **+0.49个百分点** | **+0.52个百分点** |

净增8条正确识别。逐类recall为：

| 类别 | 基线 | 候选 |
| --- | ---: | ---: |
| yes | 95.56% | 94.07% |
| no | 86.91% | 88.89% |
| up | 92.35% | 93.58% |
| down | 83.46% | 83.70% |

整体提高伴随yes召回下降；本次没有对任何单类召回设置额外约束。
这是一次固定split的实测改善，不是跨训练种子/噪声场景的统计可靠性结论。
它没有超过[历史弱增强90.56%](MODEL_TRAINING_RESULTS.md)；历史checkpoint不在本次环境，
不能把本次重训基线与历史部署包视为同一组权重，也不能宣称刷新历史最好成绩。

协议、选择记录、完整混淆矩阵及模型hash见
[机器可读摘要](validation/tinycnn8_accuracy_20261014.json)。

## 3. 部署与RTL验证

最终模型包SHA256：
`2c393a3ca92f77dfbf24605f5e8047bdf116b94e8fdf356e7d2a0c384030a595`。
基线包SHA256：
`4b6a394cc0bc8cbf752577e55c1dab5738968b4d3064695cb54a0fd0e30f91ce`。

权重镜像仍为256×64-bit，基址0/9/81；FC输入160、四个raw INT32输出，Conv参数
使用逐通道Q31 multiplier/shift。包内新增的checkpoint hash/训练元数据不改变RTL消费字段。
最终模型已通过100条真实语音的融合/分层/raw-logit及逐周期对拍，详见
[融合真实语音验证](FUSED_SPEECH_VALIDATION.md)。

模型、checkpoint、数据和原始日志保留在Git忽略目录及agi项目目录；没有将数据集或大文件纳入Git。

## 4. 重现实验

```sh
python software/tools/run_accuracy_experiment.py \
  --data-dir /path/to/speech_commands --output /path/to/new-experiment \
  --epochs 50 --workers 4 --parallel 3 --device cuda --candidate-seeds 0 1 2
```

环境与数据hash见真实语音报告。`protocol.json`在实验开始前写入，`selection.json`
在test评估前保存。部署指标使用integer golden及每类logit尺度对齐；FP32准确率不代替
INT8部署准确率。若候选test低于基线，工具会如实记录负差值，不自动宣称优化成功。
