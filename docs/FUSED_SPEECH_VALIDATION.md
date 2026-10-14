# 融合核真实语音与逐周期验证（2026-10-14）

本次在 `agi`（`bn-srv0`）执行。源基线为 `8394533`，验证工具位于
`codex/fused-speech-events` 分支。RTL 计算核心未修改，新增的是仿真观测、独立检查、
真实向量导出和训练实验工具。

## 1. 真实语音对拍结果

从 Speech Commands v0.02 的官方 test split 选取100条真实语音，每类25条，
固定顺序为yes/no/up/down轮转。每条WAV、模型包、向量文件和相关源文件均记录SHA256。
本次使用按验证集选出的弱增强+0.05 label smoothing模型，详细精度对照见
[模型准确率实验](MODEL_ACCURACY_OPTIMIZATION.md)。

| 检查 | 结果 |
| --- | --- |
| 同一向量在融合核与分层核运行 | 100条全部通过，全部8个输出lane一致 |
| 与Python整数golden对比有效raw logits | 400个INT32逐位一致 |
| 融合核Pool1/Pool2与golden对比 | 80,000个INT8字节一致 |
| 独立逐周期检查 | 280,600个采样边沿通过，含每任务的接受start边沿 |
| 融合核心周期 | 每任务2,805；C1+P1=1,000，C2+P2=1,480，FC=325 |
| 每任务事务 | 权重行760，阵列issue/retire各2,440，池化量化写回800字节 |
| 峰值阵列在途事务 | 11 |
| 故障注入 | 10项全部按预期拒绝 |

完整摘要见 [机器可读结果](validation/tinycnn8_fused_speech_20261014.json)。
分类精度使用各类别`fc_cmp_mult/shift`对齐后的logits；100条样本的分类结果只用于
此次RTL验收，不替代1,620条完整测试集精度。

## 2. 独立逐周期检查如何工作

`fused_event_trace.svh`只在仿真且定义`TINYCNN8_FUSED_EVENTS`时启用。
它在posedge的NBA更新之前采样生产/消费事件，在更新后采样profiler和logits。
每个任务cycle0表示接受start；cycle1–2805对应实际运行边沿，包含完成转移。
不会读取DUT的inflight/partial-sum缓存来生成参考值。

`check_fused_events.py`使用独立队列、外部模型参数和参考激活存储检查每个边沿：

1. 依据固定网络几何，检查权重地址、行序、bank选择和issue tag顺序。
2. 依据输入及已验证写回字节，重新生成输入地址、padding和4-byte激活向量。
3. 从已装载权重独立计算4×8点积，并要求在issue后11个边沿按原tag/bank退休。
4. 独立累加INT32部分和与bias，计算2×2最大值；禁止窗口就绪前读取。
5. 用独立整数oracle计算量化结果，要求输入后4个边沿退休并在同一边沿写回。
6. 独立维护bank占用，禁止覆盖仍在使用的权重；完成时所有队列必须排空。
7. 逐拍检查logit更新、总周期、各阶段周期、事务数和峰值占用与profiler一致。

此外锁定当前4×8核心的阶段周期1,000/1,480/325，防止总数正确却中间事件错位。
该检查器验证当前执行轨迹，适用范围是固定4×8融合网络；不用于预测任意新架构。
核心周期不包括特征前处理、host装载、CPU、AXI/DMA或IRQ处理，也不是物理时序签核。

## 3. 负向检查与合成覆盖

`test_fused_events.py`主动篡改一段真实PASS轨迹，要求检查器拒绝：
退休丢失、retire tag、阵列数值、激活地址、池化数值、写回数值、profiler计数、
量化退休提前一拍、时钟记录缺失、覆盖live权重bank。

原融合回归已接入同一检查器：21个random/impulse/extreme合成用例，每个连续两次，
共42个事件任务和33,936次元素比较；1–8类均覆盖。合成与真实证据分别标注，
`run_tinycnn8_speech_test.py`默认拒绝合成来源；只有显式`--allow-synthetic`才能跑基础设施smoke。

## 4. 复现

真实数据从[TensorFlow官方数据源配置](https://github.com/tensorflow/datasets/blob/master/tensorflow_datasets/datasets/speech_commands/speech_commands_dataset_builder.py)
对应的归档下载到本地，再上传agi。原归档2,428,923,189字节，
MD5 `6b74f3901214cb2c2934e98196829835`，
SHA256 `af14739ee7dc311471de98f5f9d2c9191b18aedfe957f4a6ff791c709868ff58`；两端MD5一致。

本次环境：Python3.11、NumPy2.4.6、PyTorch2.5.1+cu118、RTX3090、Icarus12.0。
Python/Torch只在导出和训练时需要；已导出的向量回归只需要Python标准库及iverilog/vvp。
在仓库根目录执行：

```sh
python software/tools/export_rtl_vectors.py \
  --package /path/to/selected.npz --data-dir /path/to/speech_commands \
  --output-dir /path/to/new-vectors --count 100 --seed 0

python hardware/npu/scripts/run_tinycnn8_speech_test.py \
  --vector-dir /path/to/new-vectors --output /path/to/new-results

python hardware/npu/scripts/run_tinycnn8_fused_test.py \
  --summary /path/to/fused-summary.json
```

结果目录保留编译/仿真日志、`events.csv`和`summary.json`。
各向量hash先于仿真检查；任何数值、时序、计数、来源或负向测试失败都会以非零退出。
分层100帧仿真在本次服务器上较慢；wall-clock限制与核内100,000周期超时分别检查。

本次服务器路径：

- 项目：`/data/home2/guoyiz/ChipDesign/repo/`
- 数据：`/data/home2/guoyiz/ChipDesign/data/speech_commands/`
- 真实向量：`/data/home2/guoyiz/ChipDesign/work/real-speech-vectors/`
- 真实结果：`/data/home2/guoyiz/ChipDesign/work/real-speech-validation/`
- 模型实验：`/data/home2/guoyiz/ChipDesign/work/model-accuracy-expanded/`

没有重跑整SoC的ModelSim、Vivado综合或上板。原Windows PowerShell真实向量入口默认
仍是分层参考；验证当前融合默认核心应使用上述双核心Python入口。
