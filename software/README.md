# 主机软件

当前设计已升级 ABI 5：8×8 PE、128 描述符、双 SRAM 共享激活空间；旧 ABI 4 二进制必须重编译。当前实现与验证以仓库 `docs/SOC_EXPANSION_20261106.md` 为准，下面旧版测量只对应历史源码。
当前硬件使用 host/ 下的 ABI 5.0 INT4/u4 模型编译器和驱动；七个发布模型及训练/评估入口位于 zoo/。
统一版本与证据见 [main baseline](../docs/BASELINE_MAIN_20261106.md) 和 [验证流程](../docs/VALIDATION_FLOW.md)。
train/、tools/ 中的历史训练及音频预处理工具保留供参考。

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
旧 CPU 固件和工作负载源码可从 Git 历史获取。
完整模型格式和寄存器见 docs/CPU_LESS_CONTROL.md。
