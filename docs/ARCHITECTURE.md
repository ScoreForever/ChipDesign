# ChipDesign 系统架构与规格

## 1. 概述

ChipDesign 是一个面向边缘 AI 推理的教学/原型 SoC，核心目标是把一个可配置的 INT8 NPU（Matrix Unit + Vector Unit）与一个 RISC-V CPU 集成在一起，形成“CPU 控制 + NPU 加速”的完整计算系统。

### 1.1 设计目标

- **可配置性**：NPU 阵列尺寸、数据位宽、lane 数均可参数化。
- **模块化**：计算单元、总线、存储、调试各子系统独立，便于替换或扩展。
- **可仿真**：前端功能仿真完整，支持 Icarus（NPU 单元）和 ModelSim（完整 SoC）。
- **可扩展**：为后续添加 SRAM/LSU/AGU、DMA、requantization 等留下接口。

### 1.2 当前范围

本阶段已完成：

- `cv32e40p` RISC-V CPU（RV32IMC，无 FPU）
- AXI4 交叉开关 + OBI→AXI adapter
- BootRAM（64 B，上电跳转 SRAM）
- SRAM（8 KiB 行为模型，用于加载测试程序）
- RISC-V Debug Module + JTAG DMI
- Weight-Stationary Matrix Unit（默认 4×8，INT8×INT8→INT32）
- INT8 SIMD Vector Unit（默认 8 lanes，ADD/SUB/MAX/MIN/MOV + VACC）
- NPU MMIO 桥接器（把 CPU 的 32-bit 内存访问转成 NPU 流式接口）

## 2. 顶层架构

```
                              +------------------+
clk / rst_ni / tck / tms / td |   my_soc_top     |
                              +--------+---------+
                                       |
        +------------------------------+------------------------------+
        |                              |                              |
   +----v-----+                  +-----v-----+                  +-----v-----+
   | cv32e40p |                  |  bootram  |                  |   SRAM    |
   |   CPU    |                  |  (64 B)   |                  |  (8 KiB)  |
   +----+-----+                  +-----------+                  +-----------+
        |                                                              |
        |  OBI                                                         |
        |                                                              |
        +---------------------> AXI xbar <-----------------------------+
                                       |
              +------------------------+------------------------+
              |                                                 |
        +-----v------+                                  +-------v-------+
        |    Debug   |                                  | NPU MMIO      |
        | JTAG + DM  |                                  | Wrapper       |
        +------------+                                  +---------------+
                                                               |
                                          +--------------------+--------------------+
                                          |                    |                    |
                                     +----v----+          +----v----+          +----v----+
                                     | Matrix  |          | Vector  |          | (reserved|
                                     |  Unit   |          |  Unit   |          |  LSU/DMA)
                                     +---------+          +---------+          +---------+
```

### 2.1 主数据流

1. CPU 从 BootRAM 启动，跳转到 SRAM 执行程序。
2. 程序通过 AXI 总线访问 NPU MMIO 寄存器，配置并启动 NPU。
3. CPU 把权重、激活、部分和写入 NPU MMIO 区域。
4. NPU wrapper 把 32-bit 写入组装成 Matrix/Vector Unit 需要的流式数据。
5. NPU 完成计算后，CPU 读取输出结果。
6. 对于大型网络，后续会加入 LSU/AGU + SRAM/DMA 来自动搬运数据（本阶段未实现）。

## 3. 地址映射

| 区域 | 基址 | 长度 | 说明 |
| --- | --- | --- | --- |
| Debug | 0x0000_0000 | 0x0000_1000 | RISC-V Debug Module 寄存器 |
| BootRAM | 0x0001_0000 | 0x0001_0000 | 启动代码，复位后 PC 指向此处 |
| NPU | 0x7000_0000 | 0x0000_4000 | NPU MMIO 寄存器（16 KiB） |
| SRAM | 0x8000_0000 | 0x1000_0000 | 主存（模型 8 KiB） |

### 3.1 NPU MMIO 寄存器表

偏移相对于 `NPU_BASE = 0x7000_0000`。

| 偏移 | 名称 | 宽度 | 访问 | 说明 |
| --- | --- | --- | --- | --- |
| 0x0000 | MATRIX_CTRL | 32 | W | bit0=start compute；bit1=load weights |
| 0x0004 | MATRIX_STATUS | 32 | R | bit0=idle；bit1=weights_loaded；bit2=out_valid |
| 0x0008 | VECTOR_CTRL | 32 | W | `{dst_sel, src_b_sel, src_a_sel, opcode}` |
| 0x000C | VECTOR_LANE_MASK | 32 | W | lane 掩码，bit i 控制 lane i |
| 0x0010 | VECTOR_SCALAR | 32 | W | INT8 标量 broadcast 值 |
| 0x0014 | VECTOR_STATUS | 32 | R | bit0=out_valid |
| 0x0018 | VECTOR_OP | 32 | W | 写任意值触发一次向量运算 |
| 0x0020 | VECTOR_SRC_A_LO | 32 | W | `vec_a[31:0]` |
| 0x0024 | VECTOR_SRC_A_HI | 32 | W | `vec_a[63:32]`（默认 8 lanes） |
| 0x0028 | VECTOR_SRC_B_LO | 32 | W | `vec_b[31:0]` |
| 0x002C | VECTOR_SRC_B_HI | 32 | W | `vec_b[63:32]` |
| 0x0030 | VECTOR_OUT_LO | 32 | R | `vec_out[31:0]` |
| 0x0034 | VECTOR_OUT_HI | 32 | R | `vec_out[63:32]` |
| 0x0040 | MATRIX_WEIGHT[0] | 32 | W | 权重 staging |
| ... | ... | 32 | W | ... |
| 0x005C | MATRIX_WEIGHT[7] | 32 | W | 默认 4×8 共 8 个 word |
| 0x0100 | MATRIX_ACT | 32 | W | 4 个 INT8 激活值打包 |
| 0x0110 | MATRIX_PSUM[0] | 32 | W | 8 个 INT32 部分和 |
| ... | ... | 32 | W | ... |
| 0x012C | MATRIX_PSUM[7] | 32 | W | |
| 0x0200 | MATRIX_OUT[0] | 32 | R | 8 个 INT32 输出 |
| ... | ... | 32 | R | ... |
| 0x021C | MATRIX_OUT[7] | 32 | R | |

## 4. 子系统规格

### 4.1 CPU 子系统

- **核**：`cv32e40p_top`（PULP Platform）
- **ISA**：RV32IMC
- **接口**：OBI（Open Bus Interface）用于指令和数据
- **时钟**：与 SoC 同频单时钟
- **复位**：低电平有效同步复位，受 `rstgen` 和 debug `ndmreset` 共同控制
- **启动地址**：`BOOT_BASE = 0x0001_0000`
- **中断**：目前全部 tie 0，未接外设中断
- **调试**：通过 RISC-V Debug Module + JTAG DMI 支持 halt/resume

### 4.2 总线子系统

- **协议**：AXI4（由 PULP 提供的 `axi_xbar`、`axi_adapter`、`axi2mem` 构成）
- **拓扑**：1 个 AXI crossbar，3 个 slave port（CPU instr、CPU data、DM master），4 个 master port（bootram、SRAM、debug、NPU）
- **地址译码**：由 `addr_decode` 根据 `my_soc_pkg` 中的 `addr_map` 完成
- **ID 宽度**：slave 2-bit，master 4-bit（adapter 扩展）
- **Outstanding**：每端口最多 1 笔事务（MaxMstTrans=1, MaxSlvTrans=1）

### 4.3 存储子系统

#### BootRAM

- 容量：16 words × 32 bit = 64 B
- 行为：异步读、同步写（或组合读，取决于实现）
- 内容：复位后第一条指令为 `lui t0, 0x80000; addi t0, t0, 0; jr t0`，即跳转到 SRAM_BASE

#### SRAM

- 模型容量：8 KiB（由 `linker.ld` 决定）
- 位宽：32-bit
- 字节使能：支持
- 初始化：通过 `INIT_FILE` 参数加载 `.hex` 文件
- 实际硬件实现需替换为 BRAM（FPGA）或 SRAM 宏（ASIC）

### 4.4 Debug 子系统

- **DM**：`dm_top`（PULP riscv-dbg）
- **DMI 传输**：`dmi_jtag` + `dmi_jtag_tap`
- **功能**：支持外部 JTAG debugger 连接、CPU halt/resume、复位控制
- **当前状态**：RTL 已集成，JTAG 端口已引出到顶层，但尚未做 debugger 联调测试

### 4.5 NPU 子系统

#### Matrix Unit

- **算法**：Weight-Stationary，一事务计算一个输出向量
  ```
  P_out[c] = P_in[c] + Σ_r A[r] * W[r][c]
  ```
- **默认配置**：4 rows × 8 cols
- **数据类型**：
  - 激活（A）：signed INT8
  - 权重（W）：signed INT8
  - 部分和（P_in/P_out）：signed INT32
- **接口**：流式 ready/valid
- **关键信号**：
  - `weight_start_valid/ready`：开始加载权重 tile
  - `weight_valid/ready`：流式加载 `ARRAY_ROWS` 行权重
  - `weights_loaded`：当前 tile 权重可用
  - `idle`：模块空闲
  - `in_valid/ready`：计算输入
  - `out_valid/ready`：计算输出
- **延迟**：从输入被接受到输出产生，`ARRAY_ROWS + ARRAY_COLS - 2` 个使能周期
- **特性**：
  - 无 compute/load 重叠
  - 无双缓冲
  - 支持 output backpressure（`out_ready` 拉低时冻结流水线）

#### Vector Unit

- **类型**：INT8 SIMD ALU
- **默认配置**：8 lanes
- **操作**：ADD（饱和）、SUB（饱和）、MAX、MIN、MOV
- **数据源**：`vec_a` / VACC / `vec_b` / scalar broadcast
- **目的**：OUTPUT / VACC
- **接口**：流式 ready/valid，单周期延迟
- **特性**：
  - lane mask
  - 向量累加器 VACC（用于 2×2 MaxPool/MinPool、ReLU、clamp）
  - 支持 backpressure

#### NPU MMIO Wrapper

- 作用：把 `axi2mem` 给出的 32-bit 内存访问转换为 Matrix/Vector Unit 的流式控制
- 读延迟：1 个时钟周期（寄存器读，与 `axi2mem` 期望的 memory latency 匹配）
- 内部状态机：
  - Matrix：IDLE → LOAD_WEIGHT_START → LOAD_WEIGHT_STREAM → IDLE → COMPUTE → WAIT_OUT
  - Vector：IDLE → ISSUE → WAIT_OUT

## 5. 数据格式与打包

### 5.1 Matrix Unit

#### 权重加载

- 每行权重 = `ARRAY_COLS × WGT_WIDTH` bit
- 默认 4×8 = 64 bit/行 = 2 个 32-bit word
- 行 r 的权重 `W[r][c]` 位于 `weight_data[c*8 +: 8]`
- 写入时 word 0 对应 `W[r][0..3]`，word 1 对应 `W[r][4..7]`

#### 激活输入

- `in_act_data = {A[3], A[2], A[1], A[0]}`（默认 4 rows）
- `A[r]` 位于 `in_act_data[r*8 +: 8]`

#### 部分和 / 输出

- `P[c]` 位于 `in_psum_data[c*32 +: 32]` / `out_psum_data[c*32 +: 32]`
- lane 0 在最低有效位

### 5.2 Vector Unit

- `vec_a` / `vec_b` / `vec_out` 均为 `LANES × DATA_WIDTH` bit
- lane i 位于 `[i*8 +: 8]`
- 默认 8 lanes = 64 bit，分两个 32-bit word 通过 MMIO 写入

## 6. 时钟与复位

### 6.1 时钟

- 当前为单时钟域 `clk_i`。
- JTAG 有独立的 `tck_i`，但 DMI CDC 已处理跨时钟域。
- 所有子系统共享同一个主时钟。

### 6.2 复位

- `rst_ni`：板级低电平复位，同步释放。
- `ndmreset`：来自 Debug Module 的系统级复位，可复位 CPU 和总线。
- `ndmreset_n`：`rst_ni & ~ndmreset` 经过 `rstgen` 后的复位输出。
- NPU wrapper 内部把 `rst_ni` 转成高电平有效 `rst` 给 Matrix/Vector Unit。

## 7. 配置参数

### 7.1 顶层参数

`my_soc_top`：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `INIT_FILE` | string | `"hardware/soc/sim/sw/chipdesign_npu_test.hex"` | SRAM 初始化文件 |

### 7.2 NPU 参数

`npu_mmio_wrapper` / `matrix_unit` / `vector_unit`：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `ACT_WIDTH` | 8 | 激活位宽 |
| `WGT_WIDTH` | 8 | 权重位宽 |
| `ACC_WIDTH` | 32 | 累加位宽 |
| `ARRAY_ROWS` | 4 | Matrix Unit 行数 |
| `ARRAY_COLS` | 8 | Matrix Unit 列数 |
| `LANES` | 8 | Vector Unit lane 数 |
| `DATA_WIDTH` | 8 | Vector Unit 数据位宽 |

## 8. 编程模型

### 8.1 Matrix Unit 操作流程

```c
// 1. 写权重到 MATRIX_WEIGHT[0..7]
for (int i = 0; i < 8; i++)
    NPU_WEIGHT(i) = weight_words[i];

// 2. 触发权重加载
NPU_MATRIX_CTRL = 0x2;

// 3. 等待 weights_loaded
while ((NPU_MATRIX_STATUS & 0x2) == 0);

// 4. 写激活和部分和
NPU_MATRIX_ACT = act_word;
for (int c = 0; c < 8; c++)
    NPU_MATRIX_PSUM(c) = psum[c];

// 5. 启动计算
NPU_MATRIX_CTRL = 0x1;

// 6. 等待 out_valid
while ((NPU_MATRIX_STATUS & 0x4) == 0);

// 7. 读输出
for (int c = 0; c < 8; c++)
    out[c] = NPU_MATRIX_OUT(c);
```

### 8.2 Vector Unit 操作流程

```c
// 1. 配置控制寄存器
NPU_VECTOR_CTRL = (dst_sel << 5) | (src_b_sel << 4) | (src_a_sel << 3) | opcode;
NPU_VECTOR_LANE_MASK = 0xFF;
NPU_VECTOR_SCALAR = scalar;

// 2. 写输入向量
NPU_VECTOR_SRC_A_LO = vec_a_lo;
NPU_VECTOR_SRC_A_HI = vec_a_hi;
NPU_VECTOR_SRC_B_LO = vec_b_lo;
NPU_VECTOR_SRC_B_HI = vec_b_hi;

// 3. 触发运算
NPU_VECTOR_OP = 1;

// 4. 等待输出有效（OUTPUT 目标）
while ((NPU_VECTOR_STATUS & 0x1) == 0);

// 5. 读结果
vec_out_lo = NPU_VECTOR_OUT_LO;
vec_out_hi = NPU_VECTOR_OUT_HI;
```

### 8.3 典型网络层映射

| 网络层操作 | Matrix Unit | Vector Unit |
| --- | --- | --- |
| Conv / FC | 负责 MAC 累加 | 后续 bias/requantization（待实现） |
| ReLU | — | `MAX(x, zero_point)` |
| Clamp | — | `MAX(x, lower)` + `MIN(VACC, upper)` |
| MaxPool 2×2 | — | VACC + MAX 序列 |
| MinPool 2×2 | — | VACC + MIN 序列 |
| Eltwise ADD | — | `vec_a + vec_b` |

## 9. 调试与测试

### 9.1 仿真测试

| 测试 | 工具 | 命令 | 状态 |
| --- | --- | --- | --- |
| Matrix Unit | Icarus | `hardware/npu/scripts/run_matrix_unit_test.ps1` | PASS |
| Vector Unit | Icarus | `hardware/npu/scripts/run_vector_unit_test.ps1` | PASS |
| Integrated SoC | ModelSim | `hardware/soc/sim/scripts/run_soc.ps1` | PASS |

### 9.2 调试手段

- **JTAG**：连接外部 debugger 可 halt/resume CPU。
- **VCD**：testbench 默认输出波形到 `hardware/soc/sim/out/chipdesign_soc_tb.vcd`。
- **Magic Region**：SRAM 顶部 `0x80001FE0` 用于软件向 testbench 报告 PASS/FAIL/RUNNING。

## 10. 已知限制与后续工作

### 10.1 当前限制

1. **无 LSU/AGU/DMA**：CPU 必须逐字搬运数据到 NPU MMIO，效率低。
2. **无 requantization 模块**：Matrix Unit 输出是 INT32，需要额外硬件转成 INT8。
3. **SRAM 是行为模型**：未替换为可综合存储器。
4. **无中断**：NPU 完成只能轮询，无法中断 CPU。
5. **单时钟域**：未做低功耗时钟门控（除 CPU 内部）。
6. **未做综合/时序**：仅功能仿真通过。

### 10.2 下一阶段建议

按流片推进顺序：

1. **FPGA 原型验证**：把当前 RTL 用 Vivado 综合并上板。
2. **添加 LSU/AGU + DMA**：让 NPU 能自动从 SRAM 取数。
3. **添加 Requantization Unit**：完成 INT32→INT8 转换。
4. **可综合 SRAM/BRAM**：替换行为模型。
5. **逻辑综合 + STA**：确认时序。
6. **后端物理实现**：P&R、DRC/LVS。
