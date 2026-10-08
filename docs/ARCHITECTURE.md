# KWS TinyCNN-8-Flat 芯片系统架构与软硬件边界

本文是当前生产基线的权威说明，覆盖模型计算、量化规则、NPU RTL、性能优化、
存储、SoC 接口和软硬件职责。旧六分类/GAP 方案只用于历史实验。

## 1. 系统边界

```text
麦克风或 WAV
  -> CPU：16 kHz 音频、log-mel、INT8 输入量化
  -> NPU：Conv1 -> Pool1 -> Conv2 -> Pool2 -> NHWC Flatten -> FC
  -> CPU：统一四类 logit 尺度、argmax、可选拒绝阈值和业务逻辑
```

SoC 包含 CV32E40P CPU、AXI、BootRAM、8 KiB 主 SRAM、DMA、调试模块和
NPU。NPU 不负责采样、FFT/log-mel、训练、Softmax 或业务判断。

## 2. 冻结模型

输入为 `20×16×1` signed INT8 log-mel，NHWC 排列，共 320 字节。前处理采用
16 kHz、1 秒、50 ms 帧长/帧移、1024 点 FFT、Hamming 窗、16 个 mel 通道、
80–7600 Hz，zero point=0。

| 阶段 | 运算 | 输出 |
| --- | --- | --- |
| Conv1 | `3×3 SAME, 1→8`，INT32 累加 + bias/requant/ReLU | `20×16×8` INT8 |
| Pool1 | `2×2 stride 2` MaxPool | `10×8×8` INT8 |
| Conv2 | `3×3 SAME, 8→8`，INT32 累加 + bias/requant/ReLU | `10×8×8` INT8 |
| Pool2 | `2×2 stride 2` MaxPool | `5×4×8` INT8 |
| Flatten | 直接解释连续 NHWC 存储，不搬运 | `160` INT8 |
| FC | `160→4` | 4 个 INT32 raw logits |

类别固定为 `0=yes, 1=no, 2=up, 3=down`。训练模型有 1,324 个参数；BN 在
导出时折叠进卷积权重和 bias。

卷积公式为 `acc[oc] = bias[oc] + Σ input[k] * weight[k][oc]`。INT8 乘积和
Conv1 的 9 项、Conv2 的 72 项统一在 INT32 中累加；padding 为整数 0。

## 3. 量化契约

- 激活：signed INT8、逐张量对称、zero point=0；
- 权重：signed INT8、逐输出通道对称；
- bias、partial sum、raw logit：signed INT32；
- 每个卷积输出通道独立使用 Q0.31 multiplier 和 signed 6-bit shift；
- 合法 shift `[-31,31]`，`-32` 在 MMIO commit 时拒绝；
- Conv 输出钳位 `[0,127]`；MaxPool/Flatten 不改变 scale。

```text
real_multiplier[c] = input_scale * weight_scale[c] / output_scale
biased = accumulator + bias
shifted = saturating_left_shift(biased, max(shift,0))
scaled = SaturatingRoundingDoublingHighMul(shifted, multiplier)
scaled = RoundingDivideByPOT(scaled, max(-shift,0))
result = clamp(scaled + output_offset, activation_min, activation_max)
```

FC 权重逐输出通道量化，四个 raw logit 的真实 scale 不同。CPU 必须用部署包的
`fc_cmp_mult/shift` 映射到共同尺度再 argmax。

## 4. RTL 与性能优化

```text
tinycnn8_npu_top
  +-- conv2d_engine：Conv1 / Conv2 / FC 共享
  |     +-- conv_window_addr_gen
  |     +-- matrix_unit -> ws_systolic_array -> ws_pe
  |     +-- requant_unit
  +-- maxpool2x2_engine：Pool1 / Pool2 共享
        +-- vector_unit
```

旧 `reduction_sum_unit`、`global_sum_pool_engine`、`global_avg_pool_engine`
保留独立测试，但不进入生产顶层或 SoC filelist。

远程 main 的性能优化已保留：`OPT_GATHER_LOAD` 可重叠固定 Conv2 的输入收集与
权重装载；`OPT_SPATIAL_TILE` 使用 K-major 空间分块，默认 `SPATIAL_TILE=16`。
仅固定 4×8、`10×8×8`、`3×3 SAME` Conv2 使用优化路径，其余 descriptor 自动
回退。优化复用同一 Matrix/Requant，不增加第二套计算核。

## 5. NPU 本地存储

- 激活 bank A/B：各 4096 字节；
- 权重：256×64 bit，共 2048 字节；
- 参数：Conv1、Conv2、FC 三组；
- 输出：最多 8×INT32，生产模型前 4 路有效。

```text
输入 A(320 B) -> Conv1 B(2560 B) -> Pool1 A(640 B)
 -> Conv2 B(640 B) -> Pool2 A(160 B) -> FC logits
```

Pool2 已按 NHWC 连续写入，Flatten 无额外周期和 SRAM。生产 8-lane 权重布局：

| 层 | base | word 数 | 区间 |
| --- | ---: | ---: | --- |
| Conv1 | 0 | 9 | `[0,9)` |
| Conv2 | 9 | 72 | `[9,81)` |
| FC | 81 | 160 | `[81,241)` |
| 空余 | 241 | 15 | `[241,256)` |

每次推理重装 320 字节输入；权重和参数可保留。空间 tile16 额外使用 512 B
INT32 partial sums 及少量 tag/pack；行为数组仍需在物理实现阶段替换为工艺 SRAM。

## 6. SoC 集成与存储器

生产 SoC 只实例化 `tinycnn8_npu_mmio_wrapper -> tinycnn8_npu_top`。历史算子级
wrapper 已从远程 main 删除。BootRAM 复位后跳到 `0x8000_0000`；主 SRAM 行为
模型是 2048×32 bit（8 KiB），通过 `$readmemh` 加载测试程序。该初始化方式不是
最终 ASIC 存储实现。

| 区域 | 基址 | 长度 |
| --- | --- | --- |
| Debug | `0x0000_0000` | `0x0000_1000` |
| BootRAM | `0x0001_0000` | `0x0001_0000` |
| NPU | `0x7000_0000` | `0x0000_4000` |
| SRAM | `0x8000_0000` | 8 KiB |

## 7. MMIO ABI

所有偏移相对 `NPU_BASE=0x7000_0000`。

| 偏移 | 名称 | 说明 |
| --- | --- | --- |
| `0x0000` | CONTROL | bit0 start；bit1 IRQ enable；bit2 clear done；bit3 clear error |
| `0x0004` | STATUS | ready/busy/done/error 和错误码低位 |
| `0x0008` | CLASS_COUNT | RTL 合法 1–8；生产写 4 |
| `0x000C` | ERROR_CODE | 完整错误码 |
| `0x0010..0x002C` | LOGIT[0..7] | raw signed INT32 |
| `0x0030` | VERSION | `0x0002_0001`：Flatten ABI + profiler |
| `0x0040` | PERF_TOTAL | 最近任务核心周期 |
| `0x0044..0x0058` | PERF_LAYER[0..5] | C1、P1、C2、P2、保留0、FC |
| `0x005C` | PERF_WEIGHT_ROWS | Matrix 权重行握手数 |
| `0x0060/0x0064` | PERF_ISSUES/RETIRES | Matrix 输入/输出事务数 |
| `0x0068` | PERF_PEAK_INFLIGHT | 峰值在途事务 |
| `0x006C` | PERF_STATUS | valid、overflow、busy |

性能计数从 start 后一拍到完成转移边沿；六个 lane 中 lane4 为已移除 GAP 的保留
零值，其余五层之和等于 total。新 start/reset 清零，完成后冻结，clear done 不清
profile；写性能地址返回错误6。

错误码：1非法配置，2 busy访问，3未对齐，4权重高低半字顺序，5非法 shift，
6保留/未定义操作。IRQ 在完成后锁存至 clear done。

装载窗口：

```text
输入：0x1000 + 4*i，i=0..319，低8 bit有效
权重：0x2000 + 8*i低半字；0x2004 + 8*i高半字并提交，i=0..255
参数：0x3000 + layer_id*0x100
```

参数层：0=Conv1，1=Conv2，2=旧 GAP 保留槽（commit 错误6），3=FC。层内 bias
从 `+0x00`、multiplier 从 `+0x20`、shift 从 `+0x40`，`+0x60` commit。FC 只
消费 bias。DMA 寄存器在 `0x0400..0x0410`。

## 8. 软硬件职责与验证

软件负责训练、BN 折叠、PTQ、权重打包、log-mel、输入量化、模型装载、logit
统一尺度和分类；NPU 自动完成 Conv/Pool/Flatten/FC。标准流程是检查版本、装载
241 个有效权重 word 和三组参数、逐帧装载输入、写类别4、start、等待 done/IRQ、
读取四个 logit、统一 scale、清 done。

截至 2026-10-08：强增强 INT8 accuracy/macro-F1 为 89.94%/90.00%；100 条真实
语音四个 raw logit 与 RTL bit-exact；基础、baseline/tiled 调度、MMIO 和 SoC
ModelSim 动态回归均须作为合并门槛。最终类别相同不能掩盖任何 1 LSB 差异。
