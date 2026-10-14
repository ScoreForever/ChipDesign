# KWS TinyCNN-8-Flat 芯片系统架构与软硬件边界

本文按PR #5与main冲突解决后的实现核对，更新于2026-10-14。
默认使用融合Flat核；分层Flat核保留为参考。此前7569a43的GAP/Flat接口不一致
已通过恢复160输入FC和0/9/81权重布局解决。旧GAP模块保留独立测试。

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

生产核心是 `tinycnn8_npu_fused`（`FUSED_CORE=1`，默认），分层核心
`tinycnn8_npu_top` 保留为逐层 bit-exact 参考。两者host/装载窗口相同，VERSION与PERF_LAYER解释不同。

```text
tinycnn8_npu_fused（生产）
  +-- 1 KiB 字节 bank 激活存储：每拍任意地址 4 B 读
  +-- fused_ws_array：4×8 WS 阵列，PE 双 bank 权重，bank 选择随操作数流动
  +-- 输出侧 INT32 累加（tile16 = 4 个 2×2 窗口）-> INT32 2×2 max
  +-- requant_lane：单 lane、II=1、4 级流水
```

融合核心对所有层都做 K-major tile，权重预取与计算重叠，Conv 与 MaxPool 融合，
先在 INT32 上池化再量化（multiplier ≥ 0 时逐位等价）。实测单次推理 2,805 周期，
分层核心为 59,357 周期（tile16 为 33,610）。设计、实测和验证见
`hardware/npu/TINYCNN8_FUSED.md`。

分层参考核心：

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
保留独立测试及SoC filelist编译项，但不进入当前Flat顶层实例路径。

远程 main 的性能优化已保留：`OPT_GATHER_LOAD` 可重叠固定 Conv2 的输入收集与
权重装载；`OPT_SPATIAL_TILE` 使用 K-major 空间分块，默认 `SPATIAL_TILE=16`，但两项优化开关默认0。融合核固定tile16，不使用这两个开关。
仅固定 4×8、`10×8×8`、`3×3 SAME` Conv2 使用优化路径，其余 descriptor 自动
回退。优化复用同一 Matrix/Requant，不增加第二套计算核。

## 5. NPU 本地存储

融合核心：激活只有 1 KiB。输入 `0..319`，Pool1 `320..959`，Pool2 `0..159`（覆盖
输入）。Conv 输出不落地。权重和参数布局与下文相同。以下是分层参考核心的布局：

- 分层核默认激活A/B各4096 B；MMIO参考实例设置A=640 B、B=2560 B；
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

生产 SoC 只实例化 `tinycnn8_npu_mmio_wrapper -> tinycnn8_npu_fused`
（`FUSED_CORE=0` 时为分层参考核心 `tinycnn8_npu_top`）。历史算子级
wrapper 已从远程 main 删除。BootRAM 复位后跳到 `0x8000_0000`；主SRAM为2048×32 bit（8 KiB）。生产SoC保持main的`PIPELINED=1`和
AXI `READ_LATENCY=3`：请求输入、RAM读取、同步输出三级；背压期间保持读数据。
复位屏蔽新请求，已接受请求可完成，SRAM内容保留。仿真用`$readmemh`加载程序，
综合时关闭预载；FPGA程序运行时装入。该实现不是ASIC工艺SRAM。

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
| `0x0030` | VERSION | `0x0002_0002`：融合核心；`0x0002_0001`：分层参考核心 |
| `0x0040` | PERF_TOTAL | 最近任务核心周期 |
| `0x0044..0x0058` | PERF_LAYER[0..5] | C1、P1、C2、P2、保留0、FC |
| `0x005C` | PERF_WEIGHT_ROWS | Matrix 权重行握手数 |
| `0x0060/0x0064` | PERF_ISSUES/RETIRES | Matrix 输入/输出事务数 |
| `0x0068` | PERF_PEAK_INFLIGHT | 峰值在途事务 |
| `0x006C` | PERF_STATUS | valid、overflow、busy |

性能计数从 start 后一拍到完成转移边沿；六个 lane 中 lane4 为已移除 GAP 的保留
零值，其余五层之和等于 total。融合核心把 P1/P2 计入 C1/C2，P1/P2 lane 读 0。新 start/reset 清零，完成后冻结，clear done 不清
profile；写性能地址返回错误6。

错误码：1非法配置，2 busy访问，3未对齐，4权重高低半字顺序，5非法 shift
（融合核心下，卷积层 multiplier 为负也报 5），
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
ModelSim动态回归属于部署验收项。本次合并可执行验证与未运行项见第9节；
最终类别相同不能掩盖任何1 LSB差异。

## 9. PR #5 与 main 的冲突解决（2026-10-14）

- 默认融合核保留4×8双bank权重、输出侧累加、INT32池化和单lane II=1量化。
- 分层参考恢复Flat计算图，同时保留main的八银行激活组织和独立A/B容量参数。
  每银行同步写/异步读，`bank=addr%8`、`row=addr/8`，MMIO实例为640/2560 B。
- 分层`requant_unit`采用并行参考接口，融合`requant_lane`采用4级流水、II=1接口；
  两者保留main的32位Q31舍入简化`product[62:31]+product[30]`及INT32_MIN饱和特例。
- main的AXI/SRAM三拍读、FPGA顶层、配置约束和DRC脚本保留。
- 原GAP容量测试改为`tb_tinycnn8_capacity`，检查非零Flat网络、A=639/B=2559边界、
  4/6/8类连续推理和默认/非对称容量；结构回归入口同步更新。
- CI增加融合核与两种MMIO核心选择回归；原分层调度实验继续验证参考路径。

此前FPGA资源和DRC数字属于GAP配置；默认核心更换后必须重新运行Vivado才能
确认新的LUT/FF/DSP和时序。本机没有ModelSim/Vivado，本次不声明整SoC或综合通过。
历史训练/100条真实语音结果保留为当时证据，不替代本次回归。

本次本地验证：融合21个golden用例共33,936次比较（每次2,805周期），
两种MMIO核心选择及分层容量640/2560、4096/4096均通过；完整分层调度回归
通过，共224,292次逐元素比较。融合与分层回归已接入GitHub Actions。
