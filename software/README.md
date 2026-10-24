# 主机软件

训练和音频预处理工具保留在 train/、tools/；当前硬件使用 host/ 下的 ABI 2 模型编译器和驱动。

```powershell
python software/host/make_demo.py --output build/npu_demo
python software/host/npu_program.py software/models/tinycnn8_flatten_strong.npz --output build/trained_program
python software/host/nocpu_jtag.py infer --package build/trained_program --input frame.bin --output logits.bin
```

第二条需要 NumPy；基础 JSON 编译和驱动只依赖 Python 标准库。
frame.bin 为已量化的特征，硬件不处理 PCM 音频。原始 logits 的 common-scale 转换和分类由主机完成，
所需 labels/fc_cmp_* 信息保存在模型包 manifest.metadata。旧 CPU MMIO driver 不再适用。
runtime/、firmware/kws_baseline/ 及 tools/build_kws_workload.py 为旧 CPU 工作负载源码，
依赖已移除的 MMIO 接口，不能用于当前硬件；其历史性能日志分析工具仅用于阅读旧实验。
完整模型格式和寄存器见 docs/CPU_LESS_CONTROL.md。
