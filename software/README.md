# 主机软件

训练和音频预处理工具保留在 train/、tools/；当前硬件使用 host/ 下的 ABI 3.0 INT4 模型编译器和驱动。

```powershell
python software/host/make_demo.py --output build/npu_demo
python software/host/npu_program.py software/models/tinycnn8_flatten_strong.npz --output build/trained_program
python software/host/nocpu_jtag.py infer --package build/trained_program --input frame.bin --output logits.bin
```

第二条需要 NumPy，显式将原 INT8 NPZ 经有损 `/16` PTQ 转为 INT4，不能沿用原 INT8 的精度结论；基础 JSON 编译和驱动只依赖 Python 标准库。
frame.bin 为已量化、低 nibble 优先打包的 INT4 特征（两个元素/byte，奇数末尾高 nibble 为0），硬件不处理 PCM 音频。
也可用 `Npu.infer_values` 接收逻辑整数列表。INT32 原始 logits 的分类及必要的通道尺度校准由主机完成。
PTQ 导入保留 labels，input_scale乘16，删除不再适用的旧 fc_cmp_*；metadata标记精度尚未验证。
建议用数据集重新校准或训练 INT4 模型。旧 CPU MMIO driver 不再适用。
模型编译默认激活容量8 KiB，模型区也为8 KiB；可用 `--act-bytes` 明确选择较小配置。
主机驱动仍以能力寄存器检查实际容量；INPUT_COMMIT会等待写缓冲刷新，JTAG命令格式不变。
runtime/、firmware/kws_baseline/ 及 tools/build_kws_workload.py 为旧 CPU 工作负载源码，
依赖已移除的 MMIO 接口，不能用于当前硬件；其历史性能日志分析工具仅用于阅读旧实验。
完整模型格式和寄存器见 docs/CPU_LESS_CONTROL.md。
