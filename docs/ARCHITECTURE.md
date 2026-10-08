# KWS-TinyCNN-8 芯片系统架构与软硬件边界

本文是当前实现的权威说明，覆盖模型计算、量化规则、NPU RTL 模块分工、
SoC 接口、软件职责以及验证边界。代码或模型导出工具发生变化时，应同步更新
本文，避免软件、RTL 和测试平台使用不同的解释。

## 1. 系统目标与范围

本项目实现面向关键词识别（KWS）的固定功能 TinyCNN-8 INT8 推理加速器。
完整芯片是一个 SoC（片上系统），包含 CV32E40P RISC-V CPU、SRAM、AXI
互连、DMA、调试模块和 NPU。NPU 是 SoC 中负责神经网络计算的专用部件。

第一版系统边界如下：

```text
麦克风/音频文件
      |
      v
CPU 软件：采样、分帧、MFCC 或 log-mel、INT8 输入量化
      |
      v
NPU 硬件：Conv1 -> Pool1 -> Conv2 -> Pool2 -> GAP -> FC
      |
      v
CPU 软件：统一各类别 logit 尺度、argmax、阈值与业务逻辑
```

NPU 不负责音频采样、FFT、MFCC、模型训练、Softmax 或最终业务判断。

## 2. TinyCNN-8 模型架构

### 2.1 输入

- 逻辑形状：`20 x 16 x 1`
- 数据类型：signed INT8
- 存储顺序：NHWC，通道维变化最快
- 输入含义：由 CPU 软件预先计算的 MFCC 或 log-mel 特征
- 对称量化零点：0

输入总大小为 `20 * 16 = 320` 字节。

### 2.2 网络逐层计算

| 阶段 | 运算 | 输出形状 | 输出类型 |
| --- | --- | --- | --- |
| 输入 | MFCC/log-mel 特征 | `20x16x1` | INT8 |
| Conv1 | `3x3`，stride 1，SAME，`1->8` | `20x16x8` | INT32 累加 |
| Conv1 后处理 | bias + requant + ReLU | `20x16x8` | INT8 `[0,127]` |
| Pool1 | `2x2` MaxPool，stride 2 | `10x8x8` | INT8 |
| Conv2 | `3x3`，stride 1，SAME，`8->8` | `10x8x8` | INT32 累加 |
| Conv2 后处理 | bias + requant + ReLU | `10x8x8` | INT8 `[0,127]` |
| Pool2 | `2x2` MaxPool，stride 2 | `5x4x8` | INT8 |
| GAP | 对 20 个空间位置求和并量化 | `1x1x8` | INT8 |
| FC | `8 -> class_count`，`class_count=1..8` | `class_count` | INT32 |

四分类模型共有 700 个参数，六分类模型共有 718 个参数。BatchNorm 在模型
导出时折叠进卷积权重和 bias，硬件中没有独立 BatchNorm 单元。

### 2.3 卷积数学定义

每个输出通道首先计算：

```text
acc[oc] = bias[oc] + sum(input[k] * weight[k][oc])
```

激活和权重均为 INT8，单次乘积可放入 INT16，但多个乘积累加以及 bias 使用
INT32。Conv1 每个输出累加 9 项，Conv2 每个输出累加 72 项。

SAME padding 的越界输入使用量化实数零；当前对称量化下即整数 0。

## 3. 冻结的量化规则

### 3.1 数据格式

- 激活：signed INT8、逐张量对称量化、zero point 为 0
- 权重：signed INT8、逐输出通道对称量化
- bias 与部分和：signed INT32
- Requant multiplier：每输出通道一个 signed INT32 Q0.31 数
- Requant shift：每输出通道一个 signed 6-bit 数，有效范围 `[-31,31]`
- `-32` 为非法配置，MMIO 参数提交时拒绝

卷积通道 `c` 的真实缩放比例为：

```text
real_multiplier[c] = input_scale * weight_scale[c] / output_scale
```

模型导出器将它编码为：

```text
real_multiplier[c] ~= multiplier[c] / 2^31 * 2^shift[c]
```

### 3.2 Requant 运算顺序

```text
biased = accumulator + bias
left_shift  = max(shift, 0)
right_shift = max(-shift, 0)
shifted = saturating_left_shift(biased, left_shift)
scaled = SaturatingRoundingDoublingHighMul(shifted, multiplier)
scaled = RoundingDivideByPOT(scaled, right_shift)
result = clamp(scaled + output_offset, activation_min, activation_max)
```

该顺序与 TFLite/gemmlowp 的传统 double-rounding 整数路径一致。Conv1 和
Conv2 的钳位范围为 `[0,127]`，同时完成 ReLU；GAP 为 `[-128,127]`。

MaxPool 不改变输入 scale。GAP 对 20 个位置先做 INT32 求和，再把固定的
`1/20` 和 FC 输入 scale 合并进 GAP 的逐通道 multiplier/shift。

### 3.3 FC 输出与分类

FC 权重继续使用逐输出通道量化，以保留精度。NPU 输出原始 INT32 accumulator，
因此不同类别的原始整数可能对应不同真实尺度，CPU 不得直接对它们做整数
`argmax`。

模型部署包必须额外携带每个类别的 `fc_compare_multiplier` 和
`fc_compare_shift`。CPU 将所有 logit 转换到共同尺度：

```text
common_score[c] = MultiplyByQuantizedMultiplier(
    raw_logit[c], fc_compare_multiplier[c], fc_compare_shift[c]);
class_id = argmax(common_score);
```

类别最多为 8，CPU 只需执行最多 8 次整数乘移。Softmax 不是判断最大类别所必需。

## 4. NPU 硬件结构与模块分工

正式 SoC 只实例化一套完整 NPU：

```text
tinycnn8_npu_top
  |
  +-- conv2d_engine ------------------ Conv1 / Conv2 / FC 共享
  |     +-- conv_window_addr_gen ----- 卷积窗口与 padding 地址
  |     +-- matrix_unit -------------- INT8 MAC 到 INT32
  |     |     +-- ws_systolic_array
  |     |           +-- ws_pe
  |     +-- requant_unit ------------- bias、缩放、舍入、钳位
  |
  +-- maxpool2x2_engine -------------- Pool1 / Pool2 共享
  |     +-- vector_unit -------------- 8-lane signed INT8 MAX
  |
  +-- global_avg_pool_engine --------- GAP
        +-- global_sum_pool_engine
        |     +-- reduction_sum_unit
        +-- requant_unit
```

### 4.1 基础计算单元

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| PE | `hardware/npu/rtl/ws_pe.sv` | 单个 INT8 乘加单元 |
| 脉动阵列 | `ws_systolic_array.sv` | 权重驻留 MAC 阵列 |
| Matrix Unit | `matrix_unit.sv` | 权重装载、阵列输入输出握手和对齐 |
| Vector Unit | `vector_unit.sv` | 8-lane INT8 MAX/MIN/ADD/SUB/MOV/VACC |
| Requant Unit | `requant_unit.sv` | 逐通道 Q31 缩放、双重舍入和饱和 |
| Reduction Sum | `reduction_sum_unit.sv` | 多位置 INT8 向量归约为 INT32 和 |

### 4.2 运算引擎

| 模块 | 职责 |
| --- | --- |
| `conv_window_addr_gen` | 遍历输出坐标、kernel坐标和输入通道，产生NHWC地址及padding标记 |
| `conv2d_engine` | 组织权重、激活和partial sum，调用Matrix Unit并完成Requant |
| `maxpool2x2_engine` | 读取四个空间位置，复用Vector Unit执行逐通道最大值 |
| `global_sum_pool_engine` | 对`5x4x8`的20个位置逐通道求和 |
| `global_avg_pool_engine` | 对GAP求和结果执行包含`1/20`的Requant |
| `fc_engine` | FC独立验证模块；正式顶层把FC映射为`1x1 Conv`以复用Matrix Unit |

### 4.3 完整顶层

`tinycnn8_npu_top.sv` 负责：

- 固定网络的层间状态机；
- Conv1、Conv2、FC 共享同一个 Matrix Unit；
- Pool1、Pool2 共享同一个 Vector Unit；
- 激活 A/B bank ping-pong；
- 权重、bias、multiplier、shift 的本地存储；
- 类别数检查；
- 最终 8-lane INT32 logits 寄存。

调度默认保留主线原路径。`OPT_GATHER_LOAD=1` 对固定 Conv2 重叠输入收集和
权重装载；`OPT_SPATIAL_TILE=1` 优先选择 K-major 空间分块，默认
`SPATIAL_TILE=16`。优化仅对 4x8、10x8 空间、Cin/Cout=8、3x3 SAME、stride=1
的 Conv2 生效，其他 descriptor 走原路径。两种优化共享原 Matrix Unit 和
Requant Unit，不实例化第二套计算核。

空间分块为每个输出位置保留 INT32 部分和，一组权重装载后服务多个位置。
issue 与 retire 独立，按实际握手记录返回结果所属的位置；本组全部退休且
Matrix idle 后才换权重，全部 K 完成后才量化。tile=16 的部分和容量为512B，
另有激活 pack 和 tag 队列。端口仍是当前组合逻辑存储接口，不能把缓存容量
等同综合面积，也不能由 RTL 周期推断实际 SRAM、频率或功耗。

### 4.4 存储布局

默认 4x8 阵列下：

- 激活 bank A：4096 字节
- 激活 bank B：4096 字节
- 权重存储：256 个 64-bit packed word，共 2048 字节
- 量化参数：按层、按输出通道保存
- 运行时仅保留一个 packed INT32 partial sum

激活流向：

```text
输入 A(320B)
 -> Conv1 B(2560B)
 -> Pool1 A(640B)
 -> Conv2 B(640B)
 -> Pool2 A(160B)
 -> GAP B(8B)
 -> FC logits
```

由于 Pool2 会覆盖 bank A，每次新推理前必须重新装入 320 字节输入。相同模型
的权重和参数可以跨多次推理保留。

权重基址：Conv1 为 0，Conv2 为 32，FC 为 192。默认 4x8 SoC 中每层只有一个
输出通道 tile，FC 权重布局不随 1～8 的类别数改变。

## 5. SoC 集成决策

### 5.1 为什么只接完整 NPU 顶层

生产 SoC 的 `my_npu_subsystem` 实例化 `tinycnn8_npu_mmio_wrapper`，后者只实例化
一个 `tinycnn8_npu_top`。CPU 不直接逐次操纵 Matrix/Vector/Requant。

旧的 `npu_mmio_wrapper.sv` 保留为底层教学和独立调试代码，但不进入生产 SoC
filelist。这样避免芯片中出现两套 Matrix/Vector/Requant，也避免 CPU 为一次
推理执行数千次底层 MMIO 操作。

### 5.2 SoC 总体组成

```text
CV32E40P CPU
      |
    AXI互连 -------- SRAM / BootRAM / Debug
      |
tinycnn8_npu_mmio_wrapper ---- DMA配置
      |
tinycnn8_npu_top
```

地址空间：

| 区域 | 基址 | 长度 |
| --- | --- | --- |
| Debug | `0x0000_0000` | `0x0000_1000` |
| BootRAM | `0x0001_0000` | `0x0001_0000` |
| NPU | `0x7000_0000` | `0x0000_4000` |
| SRAM | `0x8000_0000` | 系统映射窗口；当前实现8 KiB |

## 6. 完整 NPU MMIO 编程模型

所有偏移相对于 `NPU_BASE = 0x7000_0000`。

### 6.1 控制、状态和输出

| 偏移 | 名称 | 访问 | 说明 |
| --- | --- | --- | --- |
| `0x0000` | CONTROL | R/W | bit0=start脉冲；bit1=IRQ使能；bit2=清done；bit3=清error |
| `0x0004` | STATUS | R | bit0=ready；bit1=busy；bit2=done；bit3=error；bits7:4=error code低4位 |
| `0x0008` | CLASS_COUNT | R/W | 合法范围1～8，只能在idle时修改 |
| `0x000C` | ERROR_CODE | R | 完整8位错误码 |
| `0x0010..0x002C` | LOGIT[0..7] | R | 最终原始signed INT32 logits |
| `0x0030` | VERSION | R | 当前值`0x0001_0001` |
| `0x0040` | PERF_TOTAL | R | 最近一次已接受任务的核心周期 |
| `0x0044..0x0058` | PERF_LAYER[0..5] | R | Conv1、Pool1、Conv2、Pool2、GAP、FC 周期 |
| `0x005C` | PERF_WEIGHT_ROWS | R | 阵列接受的权重装载行数，包含补零行 |
| `0x0060` | PERF_MATRIX_ISSUES | R | Matrix 接受的输入事务数 |
| `0x0064` | PERF_MATRIX_RETIRES | R | Matrix 接收完成的输出事务数 |
| `0x0068` | PERF_PEAK_INFLIGHT | R | 输入已接受但输出未退休的事务峰值 |
| `0x006C` | PERF_STATUS | R | bit0=valid；bit1=overflow；bit2=busy |

性能计数排除接受 start 的 IDLE 边沿，从下一拍开始计数每个层 START/WAIT
状态，包含引擎完成时的层间转移边沿。无饱和时六个层周期之和等于总周期。
计数器在接受新的 start 或 reset 时清零，完成后冻结；清 done 不抹除统计，
被拒绝的 start 不清零。32位计数饱和于 `0xffffffff`，继续递增时置 sticky
overflow。执行期间读取为实时进度，valid=1 表示完整完成快照。

这些计数不包含模型装载、CPU/AXI/DMA或音频前处理时间；权重装载指内部
Matrix 行握手，不是主机写权重窗口的事务。性能地址只读，写入仍按错误码6处理。

错误码：

| 值 | 含义 |
| --- | --- |
| 1 | 类别数非法或start时配置无效 |
| 2 | NPU busy或start尚待接收时发生不允许的访问 |
| 3 | MMIO装载地址未按32位对齐 |
| 4 | 64位权重没有按低32位、再高32位的顺序写入 |
| 5 | 参数中出现保留的shift `-32` |
| 6 | 未定义的写地址或参数子地址 |

`done` 和 NPU IRQ 都会锁存，直到软件写 CONTROL.bit2。新的start也会清除旧done。
NPU完成中断连接CPU `irq_i[16]`；DMA完成中断连接 `irq_i[17]`。

### 6.2 输入窗口

```text
0x1000 + 4*i，i=0..319
```

每个32位写操作的低8位写入一个INT8输入。当前接口为了控制逻辑简单，没有把
四个输入字节打包到一个总线word，因此DMA源数据也应按“一字节占一个32位word”
展开。这是后续可以优化的带宽点，不影响计算语义。

### 6.3 权重窗口

```text
0x2000 + 8*i：packed weight word i 的低32位
0x2004 + 8*i：packed weight word i 的高32位，并提交完整64位
i=0..255
```

软件和DMA必须按低半字后高半字的顺序写同一个权重word。

### 6.4 参数窗口

每层占 `0x100` 字节：

```text
layer_base = 0x3000 + layer_id * 0x100
```

| layer_id | 参数组 |
| --- | --- |
| 0 | Conv1 |
| 1 | Conv2 |
| 2 | GAP |
| 3 | FC |

层内偏移：

| 偏移 | 含义 |
| --- | --- |
| `+0x00 + 4*c` | 通道c的INT32 bias |
| `+0x20 + 4*c` | 通道c的INT32 Q0.31 multiplier |
| `+0x40 + 4*c` | 通道c的signed 6-bit shift，放在低6位 |
| `+0x60` | 写任意值，将暂存参数提交到该层 |

参数暂存寄存器在各层之间复用，因此软件必须按“写完整一层参数，然后commit”
的顺序操作。GAP忽略bias，FC当前只使用bias。

### 6.5 DMA寄存器

| 偏移 | 名称 | 说明 |
| --- | --- | --- |
| `0x0400` | DMA_SRC | 源字节地址 |
| `0x0404` | DMA_DST | 目的字节地址 |
| `0x0408` | DMA_LEN | 复制的32-bit word数 |
| `0x040C` | DMA_CTRL | bit0=start，bit1=irq enable |
| `0x0410` | DMA_STATUS | bit0=busy，bit1=done；写bit1清done |

当前DMA单次只允许一个未完成事务，逐个32位word复制。

## 7. 软件与硬件职责边界

| 工作 | 软件负责 | 硬件负责 |
| --- | --- | --- |
| 模型训练 | 网络训练、QAT/PTQ、准确率评估 | 不负责 |
| 模型导出 | BN折叠、INT8量化、生成权重/bias/multiplier/shift、打包布局 | 按冻结格式消费参数 |
| 音频前处理 | 采样、分帧、窗函数、FFT、MFCC/log-mel、输入量化 | 不负责 |
| 模型装载 | CPU/DMA按MMIO格式写输入、权重和参数 | 本地存储并检查部分非法访问 |
| 网络调度 | 只发一次start并等待done | 自动执行所有卷积、池化、GAP和FC |
| 数值运算 | 不参与网络中间层 | INT8乘法、INT32累加、逐通道Requant和饱和 |
| 最终分类 | 将各类INT32 logit统一尺度，再argmax/阈值判断 | 输出原始INT32 logits |
| 中断 | 配置、响应和清除 | 完整推理结束后锁存IRQ |

软件模型导出器、Python黄金模型和RTL必须共享同一套Requant函数。任何逐层结果
相差1个LSB都视为不一致，不能用最终类别恰好相同来掩盖。

## 8. 推荐的软件执行流程

```text
1. 复位后检查VERSION和STATUS.ready
2. 装载权重和四组参数；同一模型只需装载一次
3. 为每次推理装载320个INT8输入
4. 写CLASS_COUNT
5. 写CONTROL：start=1，irq_enable按需设置
6. 等待STATUS.done或CPU irq_i[16]
7. 读取有效LOGIT
8. 用模型包中的FC比较参数统一尺度并argmax
9. 写CONTROL.bit2清done，再装入下一帧输入
```

## 9. 验证状态与尚未完成事项

当前自动回归覆盖：

- PE、Matrix Unit、Vector Unit；
- TFLite风格Requant随机测试和backpressure；
- 卷积地址、Conv1、Conv2、Pool1、Pool2、GAP、FC；
- 4x8和4x4完整TinyCNN-8合成参数推理；
- 生产MMIO wrapper的非法类别、非法shift、逐通道不同shift、IRQ和logit读取；
- CPU侧全零输入/权重的整机测试镜像；
- ModelSim对SoC完整filelist编译（0 error）；
- Verilator完整SoC静态lint，以及Yosys完整NPU层次/过程/连线检查。

本机当前没有可用的ModelSim SE仿真许可证，因此CPU通过AXI访问NPU的整机动态
测试尚未实际启动；这不影响已完成的ModelSim编译和NPU独立动态回归，但在取得
合法许可证后必须补跑`hardware/soc/sim/scripts/run_soc.ps1`。

仍需在训练完成后补齐：

- 真实模型权重、量化参数和模型包导出器；
- Python/TFLite逐层黄金向量；
- 至少100条真实语音的逐层bit-exact回归；
- 大规模分类准确率回归；
- 行为级数组替换为工艺SRAM宏；
- 综合、时序、面积和功耗评估；
- 输入MMIO/DMA四字节打包优化和跨空间位置权重复用。

在真实模型验证完成前，只能声明RTL数据通路和合成测试通过，不能声明最终KWS
准确率已经验证。
