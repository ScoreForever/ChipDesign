# TinyCNN-8-Flat 软件训练、量化与验证

当前正式软件基线是四分类 `yes/no/up/down` 的 TinyCNN-8-Flat。完整规格见
[`docs/MODEL_TRAINING_PLAN.md`](../docs/MODEL_TRAINING_PLAN.md)，硬件整数契约见
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)。

## 目录

```text
software/
  preprocess.py              # 20×16 log-mel 参考实现
  golden_model.py            # 与 RTL 逐位一致的整数网络
  train/
    model.py                 # TinyCNN-8，Flatten 为默认头
    dataset.py               # GSCD v2 四分类加载与增强
    train.py                 # 训练和 FP32 评估
    runs/                    # checkpoint（gitignore）
  tools/
    quantize.py              # multiplier/shift 与整数 requant
    export_model.py          # checkpoint -> Flatten INT8 部署包
    evaluate_quantized.py    # 完整测试集 PTQ 精度
    export_rtl_vectors.py    # 真实语音、参数和期望 logit 的 RTL 向量
  tests/                     # 软件契约回归
```

## 环境与数据

脚本使用本机 `D:\conda\envs\ml\python.exe`。GSCD v2 放在
`data/speech_commands/`；数据、checkpoint 和导出包默认不提交 Git。

## 默认训练

以下命令默认等价于 `--task four_class --head flatten --augmentation strong`：

```powershell
D:\conda\envs\ml\python.exe software/train/train.py `
  --data-dir data/speech_commands --epochs 50 --batch-size 256 `
  --output software/train/runs/tinycnn8_4class_flatten_strong
```

弱增强只需增加 `--augmentation weak` 并使用独立输出目录。`gap` 头和
`six_class` 任务仍可用于复现实验，但导出器会拒绝它们，避免与生产 RTL 混用。

## 导出与精度评估

```powershell
D:\conda\envs\ml\python.exe software/tools/export_model.py `
  --checkpoint software/train/runs/tinycnn8_4class_flatten_strong/best.pt `
  --data-dir data/speech_commands --calib-size 2048 `
  --output data/tinycnn8_flatten_strong.npz

D:\conda\envs\ml\python.exe software/tools/evaluate_quantized.py `
  --package data/tinycnn8_flatten_strong.npz `
  --data-dir data/speech_commands
```

部署包中的生产权重镜像为 256 个 64-bit word；有效布局是 Conv1 `[0,9)`、
Conv2 `[9,81)`、FC `[81,241)`。

## 真实模型 RTL 对拍

```powershell
D:\conda\envs\ml\python.exe software/tools/export_rtl_vectors.py `
  --package data/tinycnn8_flatten_strong.npz `
  --data-dir data/speech_commands `
  --output-dir data/rtl_vectors_flatten --count 100

powershell -ExecutionPolicy Bypass `
  -File hardware/npu/scripts/run_tinycnn8_trained_test.ps1 `
  -VectorDir data/rtl_vectors_flatten
```

该测试把真实输入、训练权重和量化参数送入 RTL，并逐样本比较四个 raw INT32
logit。分类精度使用 `fc_cmp_mult/shift` 在 CPU/黄金模型侧统一尺度后统计。
