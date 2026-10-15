# TinyCNN-8-Flat 软件训练、量化与验证

2026-10-14：保持RTL兼容的弱增强+label smoothing训练对照，在本次重训基线上
将INT8 test准确率从89.57%提高到90.06%。未超过历史最好90.56%；完整协议与边界见
[模型准确率实验](../docs/MODEL_ACCURACY_OPTIMIZATION.md)。选中模型已通过
[融合核真实语音与逐周期验证](../docs/FUSED_SPEECH_VALIDATION.md)。

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

当前融合/分层双核心验证入口（带来源hash和独立逐周期检查）：

```sh
python3 hardware/npu/scripts/run_tinycnn8_speech_test.py \
  --vector-dir /path/to/exported-vectors --output /path/to/new-results
```

下面的PowerShell入口仍默认验证分层参考核。

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

## 裸机 C 固件与整 SoC KWS workload

`drivers/npu_mmio.h` 封装冻结的 MMIO ABI；`runtime/kws.c` 用 CPU 逐项写入
241 个权重 word 和 Conv1/Conv2/FC 参数，逐帧装入 320 个 INT8 特征，轮询完成，
回读四个 raw logit，再按部署包 `fc_cmp_mult/shift` 统一尺度并取 argmax。
权重和参数仅装载一次，不使用 DMA。音频和 log-mel 前处理在离线完成，固件不运行 FFT。

需要 Python 3.10、numpy、torch（仅合成包生成用）以及 RISC-V GCC。
构建默认使用 `D:\tools\xpack-riscv-none-elf-gcc-15.2.0-1\bin\riscv-none-elf-`；
其他位置可用 `--tool-prefix` 指定。编译参数为 `-march=rv32imc -mabi=ilp32 -Os`。
CSR 指令局部启用汇编器的 Zicsr 支持；启动时清 `mcountinhibit`，启用 CPU 周期计数。

在仓库根目录运行，先生成可复现的合成包及随机输入：

```powershell
$py = 'D:\tools\venv-ml\Scripts\python.exe'
& $py software/tools/make_kws_synthetic.py --output build/kws_synthetic --seed 5
& $py software/tools/build_kws_workload.py `
  --package build/kws_synthetic/package.npz `
  --inputs build/kws_synthetic/inputs.npy --output build/kws_workload
```

也可直接使用训练部署包，并将 `export_rtl_vectors.py` 输出的 `inputs.hex` 传给
`--inputs`，或传入形状为 `(N,320)` / `(N,20,16)` 的 INT8 `.npy`。
真实语音先按前文命令导出测试集向量；构建会按原顺序选取能放入的最大帧数。
至少需要两帧。构建会验证部署包权重镜像与黄金模型权重一致，并生成：

- `workload.elf/.bin/.hex`、`workload.map/.dump`、`size.txt`、`symbols.txt` 和栈用量 `.su`；
- 供编译的 C 数据、选中的 `inputs.npy`、冻结的 `package.npz`；
- 含地址、大小、样本数与制品 SHA-256 的 `manifest.json`，以及逐个样本数的链接结果 `fit_attempts.json`。

所有制品均在 `build/`。hex 为完整 2048 行、每行一个小端解释的 32-bit SRAM word。
链接器从大到小尝试样本数，遇到结果区或栈重叠则拒绝该大小。
SRAM 布局按以下顺序排列，具体地址以每次构建的 manifest 为准：

| 区域 | 说明 |
|---|---|
| `0x80000000` 起 | 启动代码、C 代码、输入和模型只读数据 |
| `__results` | 固定结果区，16 字节头部 + 每帧 40 字节 |
| `0x80001ee0..0x80001fdf` | 256 字节栈；启动填充水位标记，完成时检查栈底 |
| `0x80001fe0..0x80001fff` | magic 状态、结果指针、schema=1、硬件错误码及保留字 |

结果头部依次是 `count/model_cycles/error/stack_used` 四个 UINT32；每帧依次为
四个 INT32 raw logit、UINT32 类别、输入装载周期、start→done CPU 周期、
读回/argmax/清 done 周期、NPU `PERF_TOTAL`、单帧端到端 CPU 周期。
CPU 段计时使用 `mcycle` 低 32 位差值，包含函数和轮询开销。端到端时间还包含
性能寄存器读取与结果写入开销；模型装载单独计时，不计入每帧端到端时间。
初始化、整批处理完毕和错误分别写 magic RUNNING/PASS/FAIL。
错误值 1–6 分别为硬件错误、轮询超时、未就绪、版本不支持、profile 无效、栈底损坏。
此无中断固件当前 GCC 的 `.su` 报告最深调用链为 112 字节；更换编译器后应复查
栈报告和反汇编中的库函数，并核对仿真回报的栈水位。

Windows 入口将当前硬件源码及选定 hex 打包上传到新的远端目录，使用 Xcelium，
并在编译或仿真失败时仍尝试回收日志。需要配置好的免交互 SSH 和服务器 EDA 环境：

```powershell
$env:SSH_HOST = 'aidesign'
powershell -NoProfile -ExecutionPolicy Bypass `
  -File hardware/soc/sim/scripts/run_kws_workload_remote.ps1 `
  -WorkloadDir build/kws_workload -TimeoutSeconds 300 -CompileTimeoutSeconds 600
$log = (Get-ChildItem build/kws_workload_*/simulation.log |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
& $py software/tools/compare_kws_workload.py `
  --workload build/kws_workload --log $log `
  --json-output build/kws_workload/comparison.json
```

已有服务器检出和 hex 时也可在 Linux 仓库根目录直接运行：

```bash
bash hardware/soc/sim/scripts/run_kws_workload_linux.sh \
  --firmware "$PWD/build/kws_workload/workload.hex" \
  --output "$PWD/build/kws_sim" --timeout 300 --compile-timeout 600
```

新 testbench 使用 50 MHz 时钟，输出 `KWS_HEADER/KWS_SAMPLE/KWS_PASS` 机器可读记录。
比对脚本校验制品哈希、完整样本序列、四个 raw logit 和统一尺度后的类别，输出每段
周期的 min/mean/max、50 MHz 下的微秒数，以及逐帧 CPU 与 NPU 周期对照。
只有完整 SoC 的真实日志通过此比对才算动态验收；单独的 firmware PASS 不代表逐位通过。

不依赖仿真的解析器自测：

```powershell
& $py software/tools/selftest_kws_workload.py --workload build/kws_workload
```

该命令生成带有人工周期的 `constructed_results.log`，验证正常比对和逐样本/逐 lane
1-LSB 错误、错误类别、重复/缺失记录、失败标记和非法计时等拒绝路径。它不能用于
性能报告或作为 SoC 仿真证据。`selftest_kws_scale.py --library <host-library>` 还可将
原生编译的 `runtime/kws.c` 共享库与 Python 定点量化参考比较，包括 INT32 饱和边界。
