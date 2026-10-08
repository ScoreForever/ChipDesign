# 权重驻留（Weight-Stationary）Matrix Unit

KWS-TinyCNN-8 NPU 已确认的模型边界、整数语义、存储和验证基线见
[TINYCNN8_ARCHITECTURE.md](TINYCNN8_ARCHITECTURE.md)。
当前生产SoC的完整接入方式、MMIO寄存器和软硬件职责边界以
[系统架构说明](../../docs/ARCHITECTURE.md)为准。

`matrix_unit` 每接受一个计算事务（transaction），计算一个输出向量：

`P_out[c] = P_in[c] + sum(r=0..ARRAY_ROWS-1) A[r] * W[r][c]`。

默认配置为 **4 行 × 8 列**，激活值（activation）和权重均为有符号 8 位整数，部分和（partial sum）为有符号 32 位整数。

- `ws_pe.sv`：保存一个权重，并执行寄存器化的乘加运算。
- `ws_systolic_array.sv`：连接各个 PE，其中激活值向右传播，部分和向下传播。
- `matrix_unit.sv`：负责权重加载、输入错位（skew）、输出去错位（deskew）以及流式计算控制。

## 接口

| 端口 | 方向 | 含义 |
| --- | --- | --- |
| `clk`, `rst` | 输入 | 上升沿时钟，以及同步高电平有效复位 |
| `weight_start_valid`, `weight_start_ready` | 输入、输出 | 用于开始一个新的权重 tile 的握手信号；仅在模块空闲时 `ready` 有效 |
| `weight_valid`, `weight_ready`, `weight_data` | 输入、输出、输入 | 每次握手提供一行打包后的权重数据 |
| `weights_loaded`, `idle` | 输出 | 当前 tile 的权重已可用于计算；当前没有正在进行的加载或计算 |
| `in_valid`, `in_ready` | 输入、输出 | 计算输入握手信号 |
| `in_act_data`, `in_psum_data` | 输入 | 打包后的激活值向量和输入部分和向量 |
| `out_valid`, `out_ready`, `out_psum_data` | 输出、输入、输出 | 打包后的输出部分和握手接口 |

Lane 0 位于最低有效位（LSB）。

激活值 `A[r]` 位于：

`in_act_data[r*ACT_WIDTH +: ACT_WIDTH]`

输入部分和 `P_in[c]` 和输出部分和 `P_out[c]` 位于：

`c*ACC_WIDTH +: ACC_WIDTH`

加载第 `r` 行权重时，该行中的权重元素 `W[r][c]` 位于：

`weight_data[c*WGT_WIDTH +: WGT_WIDTH]`

将 `weight_start_valid` 拉高一个周期，或者持续保持其为高，直到它与 `weight_start_ready` 完成握手。

随后，在每次满足：

`weight_valid && weight_ready`

时发送一行权重。总共需要发送恰好 `ARRAY_ROWS` 行，并按照 **第 0 行到第 `ARRAY_ROWS-1` 行**的顺序发送。

最后一行权重加载完成后，`weights_loaded` 会变为高电平。

只有在 `idle` 为高时才能开始加载新的 tile。当前设计**不支持计算与权重加载重叠，也不提供双缓冲（double buffering）**。

输入的第 `r` 个激活值会延迟 `r` 个使能周期（enabled cycles），而输入部分和的第 `c` 列会延迟 `c` 个使能周期。

经过这些延迟后，寄存器化的脉动阵列会使对应的数据在 PE `(r,c)` 处正确相遇。

输出的第 `c` 列还会额外延迟：

`ARRAY_COLS-1-c`

个使能周期，从而使整个输出向量重新对齐。

从一个输入在时钟边沿被接受，到对应输出在某个时钟边沿产生，流水线总共需要：

`ARRAY_ROWS+ARRAY_COLS-2`

个使能周期。

输出握手会在后续某个时钟边沿发生。

流水线填满之后，阵列每个周期都可以接受一个新的输入。

当：

`out_valid && !out_ready`

时，一个公共时钟使能信号会冻结所有数据寄存器和 valid 寄存器，同时 `in_ready` 会变为低电平。

输入气泡（bubble）仍会沿流水线向前传播，但不会使输出端的 `out_valid` 置高。

对于一个 **4×8** 的示例，控制器可以加载 4 行、每行 8 个权重。

假设：

`A=[1,2,3,4]`

`P_in[0]=10`

并且权重矩阵第 0 列为：

`[5,6,7,8]`

则：

`P_out[0]=10+1*5+2*6+3*7+4*8=80`

其余 7 列也会在同一个事务中同时完成计算。

偏置（bias）可以通过 `P_in` 提供。

对于 K/N 维度不能整除阵列尺寸的尾部（tail），由控制器使用 0 进行填充（zero padding）。

## 测试

在 Windows 环境下，从仓库根目录运行以下命令。要求系统 `PATH` 中已经包含 Icarus Verilog：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_matrix_unit_test.ps1
```

该脚本会：

- 使用 `iverilog -g2012 -Wall` 进行编译；
- 使用 `vvp` 运行仿真；
- 任意测试失败时返回非零退出码。

测试内容包括：

- PE 单元测试；
- 4×4 Matrix Unit 回归测试；
- 4×8 Matrix Unit 回归测试；
- 8×8 Matrix Unit 回归测试。

如果手动编译 Matrix Unit testbench，可以在编译时定义 `DUMP_VCD`，从而生成：

`matrix_unit.vcd`

用于波形调试。

## Vector Unit

独立的 INT8 SIMD `vector_unit` 支持饱和 ADD/SUB、MAX/MIN/MOV、scalar broadcast、
lane mask，以及用于逐 channel pooling 的 VACC。接口、opcode 编码、ready/valid
时序、VACC 行为与 pooling 操作序列见 [Vector Unit 设计与验证说明](VECTOR_UNIT.md)。
Requantization 由其他独立模块负责。

在仓库根目录执行 Icarus 回归：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_vector_unit_test.ps1
```

## Requantization Unit

`rtl/requant_unit.sv` 对每个输出通道执行 INT32 bias、Q0.31 multiplier、
TFLite/gemmlowp 双重舍入、output offset 和可配置激活钳位，输出 signed INT8。
回归测试命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_requant_unit_test.ps1
```

## 旧版 Global Pool IP（不进入当前生产顶层）

`rtl/reduction_sum_unit.sv`、`global_sum_pool_engine.sv` 和
`global_avg_pool_engine.sv` 是早期 GAP 方案留下的通用单元，仍保留独立回归，
但 TinyCNN-8-Flat 生产顶层不再实例化它们。当前 Pool2 的 `5×4×8` 数据直接
按 NHWC 展平后输入 FC。

Reduction Sum 回归命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_reduction_sum_test.ps1
```

## Conv Window Address Generator

`rtl/conv_window_addr_gen.sv` 按 NHWC 顺序生成完整卷积层的输入地址流，支持
kernel、stride、padding、通道遍历和 ready/valid backpressure。越界位置通过
`is_padding` 标记，由调用者提供量化实数零。TinyCNN-8 两个 `3×3 SAME`
卷积层以及额外的 strided VALID 配置均有自检回归：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_conv_window_test.ps1
```

## Conv2D Engine

`rtl/conv2d_engine.sv` 将窗口地址生成器、Matrix Unit 和 Requant Unit 连接成
描述符驱动的普通卷积引擎。首版采用单空间位置保留 INT32 PSUM 的保守调度，
先保证逐位正确；空间 tile 和权重跨位置复用将在此基线上优化。TinyCNN Conv1
分别映射到 4×8 和 4×4 阵列进行完整层回归：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_conv2d_engine_test.ps1
```

`rtl/maxpool2x2_engine.sv` 复用 Vector Unit 的 VACC/MAX 数据通路执行 NHWC
`2×2 stride 2` MaxPool。两个 TinyCNN 池化层均在 8-lane 和 4-lane 配置下验证：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_maxpool_test.ps1
```

旧 Global Sum Pool 测试覆盖 8/4/3 lanes：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_global_sum_pool_test.ps1
```

旧 Global Avg Pool 的 INT32 sum + requant 回归：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_global_avg_pool_test.ps1
```

`rtl/fc_engine.sv` 复用 Matrix Unit 完成 INT8 输入和权重到 INT32 logits 的
全连接层。通用单元覆盖4类/6类输出；当前生产模型固定为`160→4`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_fc_engine_test.ps1
```

## TinyCNN-8 NPU Top

`rtl/tinycnn8_npu_top.sv` 串联 Conv1、Pool1、Conv2、Pool2、NHWC Flatten 和
`FC(160→4)`；Flatten 不搬运数据，Conv1/Conv2/FC 共享同一个 Matrix Unit。
权重和量化参数可在空闲时加载，
顶层使用两块行为级激活 SRAM 做 ping-pong。运行过程中输入所在的 A bank
会被后续层复用，因此每次推理开始前，主机必须重新加载完整输入特征；权重和
量化参数可以跨同一模型的推理保留。生产8-lane权重基址为Conv1=0、Conv2=9、
FC=81，共使用241个64-bit word。端到端合成测试参数回归命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_tinycnn8_top_test.ps1
```

训练权重和真实语音的逐位回归：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File hardware/npu/scripts/run_tinycnn8_trained_test.ps1 `
  -VectorDir data/rtl_vectors_flatten
```

运行全部 NPU RTL 回归：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_all_tests.ps1
```
