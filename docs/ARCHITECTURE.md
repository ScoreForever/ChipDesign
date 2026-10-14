# TinyCNN-8 当前实现：设计细节与软硬件边界

本文按 `main` 提交 `7569a43` 的 RTL 核对，更新于 2026-10-14。
计算核、MMIO adapter 和软件导出器目前存在接口差异，不能视为统一部署基线。
源码依据为 `hardware/npu/rtl/tinycnn8_npu_top.sv`、
`hardware/soc/rtl/npu/tinycnn8_npu_mmio_wrapper.sv` 和
`software/tools/export_model.py`。

## 1. 系统边界与当前集成缺口

```text
CPU：音频前处理、20×16×1 INT8 输入
  -> MMIO wrapper -> NPU：Conv1 -> Pool1 -> Conv2 -> Pool2 -> GAP -> FC
  -> CPU：读取 INT32 logits、按模型尺度比较、分类和业务逻辑
```

SoC 包含 CV32E40P、AXI、BootRAM、8 KiB 主 SRAM、DMA、调试模块和 NPU。
NPU 不负责音频采样、FFT/log-mel、训练、Softmax 或业务判断。

| 层次 | 当前行为 | 对接限制 |
| --- | --- | --- |
| NPU 核 | GAP 分类头，FC 输入 8，支持 1–8 类 | 原生 host 端口可装载 GAP 参数 |
| MMIO wrapper | 默认 4 类，版本读回 `0x0002_0001` | layer2 commit 被拒绝，错误码6，无法装载核所需 GAP 参数 |
| 软件训练/导出 | 默认四类 `yes/no/up/down`，Flatten，FC 输入160 | 权重基址0/9/81，与核的0/32/192不兼容；导出器拒绝 GAP 头 |

核内 `mult_gap/shift_gap` 没有复位初始化。仅通过现有 MMIO 完成参数装载，
不能保证 GAP/FC 的确定数值结果。版本值沿用 Flat 分支，不能据此判断实际分类头。
这属于当前实现缺口；本次只更新文档，没有修复 RTL、ABI 或导出器。

## 2. 固定网络与数据顺序

输入为 signed INT8 `20×16×1`，共320字节。激活按 NHWC 存放，通道维最快。
当前软件前处理使用16 kHz、1秒、50 ms帧长/帧移、1024点FFT、Hamming窗、
16个mel通道、80–7600 Hz；这是软件输入契约，核本身只接收字节。

| 阶段 | RTL 运算 | 输出 | 目标区 |
| --- | --- | --- | --- |
| Conv1 | `3×3 SAME, 1→8`，bias/requant/ReLU | `20×16×8` INT8 | B，2560 B |
| Pool1 | `2×2 stride2` MaxPool | `10×8×8` INT8 | A，640 B |
| Conv2 | `3×3 SAME, 8→8`，bias/requant/ReLU | `10×8×8` INT8 | B，640 B |
| Pool2 | `2×2 stride2` MaxPool | `5×4×8` INT8 | A，160 B |
| GAP | 每通道累加20个空间值，再 requant | `1×1×8` INT8 | B，8 B |
| FC | 作为 `1×1 Conv, 8→class_count` 执行 | 1–8个 INT32 raw logits | logits 寄存器 |

GAP 的 multiplier/shift 必须同时编码除以20和输入/输出量化尺度变换。
当前核没有 Flatten 路径，不会将 Pool2 的160个值直接送给 FC。
核不存储类别名称；类别顺序由与其兼容的模型包决定。

## 3. 计算数据通路与控制

```text
tinycnn8_npu_top
  +-- conv2d_engine（Conv1/Conv2/FC 共用）
  |     +-- conv_window_addr_gen
  |     +-- matrix_unit -> ws_systolic_array -> ws_pe
  +-- maxpool2x2_engine -> vector_unit（Pool1/Pool2 共用）
  +-- global_avg_pool_engine -> global_sum_pool_engine -> reduction_sum_unit
  +-- shared_requant（Conv 与 GAP 按状态复用一套串行量化器）
```

默认 Matrix 为4行×8列。每个事务执行
`P_out[c] = P_in[c] + Σ A[r]*W[r][c]`，INT8乘积累加到INT32。
权重按行装载并驻留 PE；激活向右、部分和向下传播。输入 skew/输出 deskew
使8列结果对齐。Matrix 不支持自身的装载与计算重叠或权重双缓冲。
详细握手与使能周期见 [Matrix Unit](../hardware/npu/README_zh-hans.md)。

Conv1 的 K=9，Conv2 的 K=72，FC 的 K=8；K尾部用零填齐4行。
Conv 引擎以 bias 初始化部分和，量化器接收的额外 bias 为零，避免重复加偏置。
FC 选择 `final_output_int32`，绕过 INT8 requant，直接写 logits。

顶层状态序列为：

```text
IDLE -> C1_START/WAIT -> P1_START/WAIT -> C2_START/WAIT
     -> P2_START/WAIT -> GAP_START/WAIT -> FC_START/WAIT -> IDLE
```

START 等待算子 `start_valid && start_ready`；WAIT 等待算子 done。
只有 IDLE 且 class_count为1–8、复位无效时，顶层 start_ready才有效。
接受 start 时锁存类别数并清 logits；完成产生一拍 done，wrapper 将其锁存。
不同层不并行执行，按状态切换共享资源。核内输出写 ready 固定为1；
Matrix/Requant 等内部接口仍遵守 ready/valid 和背压。

### Conv2 可选调度

`OPT_GATHER_LOAD` 与 `OPT_SPATIAL_TILE` 默认均为0，包括当前 SoC 默认实例。
`SPATIAL_TILE=16` 只规定启用空间分块后的容量，不代表默认启用。
优化仅匹配固定4×8阵列、`10×8×8`、`3×3 SAME`、stride1的 INT8 Conv2；
其他描述符走基线。两项同时打开时，空间分块优先。

基线为单空间位置保存部分和；gather/load 可重叠激活收集与权重装载。
空间分块按 K-major 顺序处理多个输出位置，使一组已装载权重服务多个位置，
通过 tag FIFO 将退休结果写回相应 partial sum。tile16 的部分和为
`16×8×32 bit=512 B`；这是逻辑容量，不是独立 SRAM 宏或物理面积。

## 4. 定点运算与串行重定量

- 激活、权重为 signed INT8；padding为整数0，输入 zero point应为0。
- bias、部分和、raw logits为 signed INT32；加法按32位运算，模型必须避免累加溢出。
- Conv/GAP 每输出通道使用 signed Q0.31 multiplier 和 signed 6-bit shift。
- Conv钳位 `[0,127]`；GAP钳位 `[-128,127]`，offset均为0；MaxPool不改尺度。
- FC raw logits的尺度由模型权重决定，CPU比较前需要相应模型的尺度对齐参数。

```text
biased = acc + bias
shifted = saturating_left_shift(biased, max(shift,0))
scaled = SaturatingRoundingDoublingHighMul(shifted, multiplier)
scaled = RoundingDivideByPOT(scaled, max(-shift,0))
result = clamp(scaled + output_offset, activation_min, activation_max)
```

Q31高乘积舍入使用 `product[62:31] + product[30]`；
`INT32_MIN×INT32_MIN` 特例饱和到INT32_MAX。随后右移舍入使用 ties-away-from-zero。
量化器锁存整个向量，逐lane执行4个相位；8-lane向量在接受输入后32拍产生
out_valid，输出背压时保持数据。掩码关闭的lane仍走相位但输出0。
因此量化吞吐不能按8个lane并行每拍输出理解。

正常shift范围为 `[-31,31]`。核端口不检查非法shift；独立量化器对-32返回
确定的下溢0。MMIO 对Conv commit拒绝-32，FC不检查shift，GAP commit整体被拒绝。

## 5. 本地存储与地址映射

原生核默认 A/B 各4096 B；当前 MMIO wrapper 缩为 A=640 B、B=2560 B。
生命周期为：

```text
输入 A(320 B) -> C1 B(2560 B) -> P1 A(640 B)
 -> C2 B(640 B) -> P2 A(160 B) -> GAP B(8 B) -> FC logits
```

每区8个字节银行，`bank=byte_addr%8`、`row=byte_addr/8`。
每银行同步写、异步读；连续8字节映射到不同银行，读基址跨行时各银行选择
对应下一行地址，再按lane重新排列。A为每银行80字节，B为每银行320字节。
这些是行为数组，可推断 FPGA distributed RAM；不能直接视为 ASIC 工艺 SRAM。
NPU内部激活读没有改成SoC主 SRAM的三拍读协议。

8列配置的权重存储为256×64 bit=2048 B，lane0在低8位。
`word_addr = layer_base + k*ceil(output_channels/ARRAY_COLS) + output_tile`。
K按kernel y/x/input channel遍历。当前8列且输出不超过8时：

| 内容 | base | 有效word | 区间 |
| --- | ---: | ---: | --- |
| Conv1 | 0 | 9 | `[0,9)` |
| 间隔 | — | — | `[9,32)` |
| Conv2 | 32 | 72 | `[32,104)` |
| 间隔 | — | — | `[104,192)` |
| FC | 192 | 8 | `[192,200)` |
| 尾部空余 | — | — | `[200,256)` |

有效89个word；4类FC只使用低4个lane。4列核仍使用同一层base，但每个K需
两个输出tile（Conv1/2均8通道），不能套用上表有效区间。

原生参数层0/1/2/3分别为Conv1/Conv2/GAP/FC。
0/1消费bias/multiplier/shift，2只消费multiplier/shift，3只消费bias。
参数按输出tile保存；主机须在首次推理前装载权重和全部所需参数，数组不由复位清零。
每次任务需重新加载输入，权重和参数可跨任务保留。

## 6. SoC 主存与集成

正式 SoC 实例化 `tinycnn8_npu_mmio_wrapper -> tinycnn8_npu_top`；
SoC filelist包含GAP、Global Sum和Reduction模块，未实例化独立 `fc_engine`。
旧算子级wrapper已删除。

| 区域 | 基址 | 长度 |
| --- | --- | --- |
| Debug | `0x0000_0000` | `0x1000` |
| BootRAM | `0x0001_0000` | `0x10000` |
| NPU | `0x7000_0000` | `0x4000` |
| SRAM | `0x8000_0000` | 8 KiB |

主 SRAM 为2048×32 bit，生产 SoC使用 `PIPELINED=1`，
AXI适配器对应 `READ_LATENCY=3`。请求输入级、RAM读取级和同步输出级形成三拍读，
R通道等待数据并在背压时保持。复位屏蔽新请求，已接受请求可完成，SRAM内容保留。
其他AXI从设备使用默认一拍配置。仿真用 `$readmemh` 预载程序，综合时关闭预载，
FPGA程序需运行时装入。实现与验证边界见 [FPGA DRC记录](FPGA_DRC_CLOSURE.md)。

## 7. 当前 MMIO ABI

所有偏移相对 `0x7000_0000`，32位访问。

| 偏移 | 名称 | 实际行为 |
| --- | --- | --- |
| `0x0000` | CONTROL | bit0 start；bit1 IRQ enable；bit2 clear done；bit3 clear error |
| `0x0004` | STATUS | bit0 ready，bit1 busy，bit2 done，bit3 error，bits7:4错误码低位 |
| `0x0008` | CLASS_COUNT | 仅低4位参与校验，合法1–8，复位为4 |
| `0x000C` | ERROR_CODE | 完整8-bit错误码 |
| `0x0010..0x002C` | LOGIT[0..7] | signed INT32 |
| `0x0030` | VERSION | 固定 `0x0002_0001`，不能证明Flat模型兼容 |
| `0x0040` | PERF_TOTAL | 最近任务核心周期 |
| `0x0044..0x0058` | PERF_LAYER[0..5] | C1、P1、C2、P2、GAP、FC；lane4实际统计GAP |
| `0x005C` | PERF_WEIGHT_ROWS | Matrix权重行握手数 |
| `0x0060/0x0064` | PERF_ISSUES/RETIRES | Matrix输入/输出事务数 |
| `0x0068` | PERF_PEAK_INFLIGHT | 峰值在途事务 |
| `0x006C` | PERF_STATUS | bit0 valid，bit1 overflow，bit2 busy |
| `0x0400/04/08` | DMA SRC/DST/LEN | DMA源、目标、长度 |
| `0x040C` | DMA CONTROL | bit0 start，bit1 IRQ enable |
| `0x0410` | DMA STATUS | 读bit0 busy/bit1 done；写bit1 clear done |

性能计数覆盖START/WAIT状态，包括完成转移边沿，排除接受start的IDLE边沿。
六层计数之和等于total（未饱和时）；计数饱和到UINT32_MAX并置overflow。
接受新start/reset清零，完成后冻结；clear done不清profile。写性能地址报错误6。
IRQ为 `irq_enable && done_latched`；每次CONTROL写都会更新IRQ enable位。

```text
输入：0x1000 + 4*i，i=0..319，低8 bit有效
权重：0x2000 + 8*i低半字；0x2004 + 8*i高半字并提交，i=0..255
参数：0x3000 + layer_id*0x100
```

参数窗口：bias `+0x00..1C`、multiplier `+0x20..3C`、shift `+0x40..5C`，
每lane间隔4字节，`+0x60` commit。一个共用暂存组服务所有层，commit只提交tile0，
软件须完整重写需要的字段。layer2字段可以暂存，但commit始终报错误6；
这是wrapper与核GAP参数接口的缺口，并不表示GAP已从核中删除。

错误码：1非法类别/配置，2忙时start或装载/配置写，3装载窗口写未对齐，
4权重高半字无对应低半字，5Conv非法shift，6未定义操作/保留提交。
未定义读返回0；不要把装载写的对齐校验扩展理解为所有读写都统一检查。
读结果在请求边沿寄存；模型窗口只写。wrapper没有检查“模型是否已完整装载”。

## 8. 验证证据与当前使用边界

[结构优化记录](FPGA_STRUCTURE_OPTIMIZATION.md)与
[DRC修正记录](FPGA_DRC_CLOSURE.md)记录了当前GAP核的非零直接端口测试，
包含4/6/8类连续任务、非对称/默认容量和Q31算术检查。对应测试为
`tb_tinycnn8_gap_capacity.sv`，它直接装载包括layer2在内的四组参数。
记录的核内每次推理为71,302周期；它不是训练模型准确率证据。

SoC全零模型、DMA和AXI/SRAM测试属于集成smoke。全零模型不能替代非零GAP参数
经MMIO装载的端到端验证。现有Flat软件报告与100条真实语音bit-exact记录属于
此前Flat实现，不能据此声明当前GAP核已通过该部署链。

当前设计细节以RTL为准；要恢复可部署链，仍需统一分类头、权重地址、参数commit
和模型包，再运行非零MMIO/SoC与真实模型逐位回归。
本次文档更新未重新运行RTL仿真或Vivado；引用的数值为已有报告记录。
