# INT8 SIMD Vector Unit

`rtl/vector_unit.sv` 实现参数化 SIMD VU，默认 `LANES=8`、`DATA_WIDTH=8`，
每个向量为 64 bit，各 lane 按 signed INT8 解释。全部 lane 同时执行相同 opcode。
`LANES >= 1`；位宽表达式按 `DATA_WIDTH` 参数推导（须至少为 2），本阶段配置与验证范围均为 INT8。
它接收 Requantization Unit 已处理的 INT8 数据，不处理 INT32 accumulator。
Matrix Unit、requantization、SRAM/LSU/AGU、pooling 地址与窗口遍历由外部模块负责。

实现沿用现有 Matrix Unit 的 `wire/reg`、generate、同步高有效复位及 packed vector 风格。
不增加 MUL、SHIFT、SFU、独立 ReLU/comparator 或 horizontal reduction tree。

## 接口

| 端口 | 方向 | 位宽 | 语义 |
| --- | --- | --- | --- |
| `clk`, `rst` | 输入 | 各 1 | 上升沿时钟，同步高有效复位 |
| `in_valid`, `in_ready` | 输入、输出 | 各 1 | 输入数据及全部控制信息一起握手 |
| `opcode` | 输入 | 3 | 所有 lane 共享的运算编码，见下表 |
| `src_a_sel` | 输入 | 1 | `0=VECTOR_A`（`vec_a`），`1=VACC` |
| `src_b_sel` | 输入 | 1 | `0=VECTOR_B`（`vec_b`），`1=SCALAR`（广播 `scalar`） |
| `dst_sel` | 输入 | 1 | `0=OUTPUT`，`1=DEST_VACC` |
| `scalar` | 输入 | `DATA_WIDTH` | signed INT8 标量 |
| `lane_mask` | 输入 | `LANES` | bit i 为 1 时 lane i 有效 |
| `vec_a`, `vec_b` | 输入 | 各 `LANES*DATA_WIDTH` | 两个打包输入向量 |
| `vec_out` | 输出 | `LANES*DATA_WIDTH` | 打包的寄存器输出向量 |
| `out_valid`, `out_ready` | 输出、输入 | 各 1 | 输出握手 |

Lane 0 位于最低有效位；lane i 位于 `[i*DATA_WIDTH +: DATA_WIDTH]`。
当 `in_valid=1 && in_ready=0` 时，发送方须保持所有控制和输入数据稳定，直到握手或复位。
内部 `vacc` 不设置独立外部读写端口：通过 source/destination 选择进行访问。

## Opcode 与 datapath

| 编码 | RTL 常量 | 运算 |
| --- | --- | --- |
| `3'd0` | `VU_ADD` | `saturate(A+B)` |
| `3'd1` | `VU_SUB` | `saturate(A-B)` |
| `3'd2` | `VU_MAX` | `max(A,B)` |
| `3'd3` | `VU_MIN` | `min(A,B)` |
| `3'd4` | `VU_MOV` | `A`（忽略 B 及 scalar） |
| `3'd5..7` | 保留 | 接受后不写 VACC、不产生输出事务 |

选择 A/B 后，先将每个 INT8 操作数符号扩展到 9 bit。每 lane 共用一条加减通路：
ADD 使用原 B；SUB/MAX/MIN 对扩展后的 B 取反并置 carry-in，计算 `A + ~B + 1`。
MAX/MIN 依据完整 9-bit 差值的符号选择 A/B，差值范围为 -255..255。
不使用 8-bit subtraction 的 sign bit，因此 `127 - (-128)` 的比较不会出错。

ADD/SUB 检查 9-bit 中间结果的最高两位：相同表示可由 INT8 表达；不同则按
9-bit 符号钳位到 -128 或 127。MAX/MIN/MOV 直接选取 INT8 操作数，无 saturation。
核心数据通路不使用 integer 或 32-bit arithmetic；testbench 的 reference model 使用 integer。

## 时序、backpressure 与 VACC

这是一个组合 SIMD ALU 加一个输出寄存器的单级流水线。

- 在上升沿 E 满足 `in_valid && in_ready` 时接受输入。
- 若目标为 OUTPUT，结果及 `out_valid` 在 E 的寄存器更新后可见，最早在 E+1 握手。
  即输入握手到最早输出握手间隔一拍。无阻塞时吞吐率为每拍一个向量。
- `in_ready = (!out_valid || out_ready) && !rst`。已有输出在 E 消费后，可在 E
  同时接受下一输入并更新输出寄存器。
- 当 `out_valid && !out_ready` 时，`in_ready=0`，输出 data/valid 和 VACC 均保持稳定。
  这里采用保守的统一阻塞策略，包括目标为 VACC 的操作也暂停。
- VACC 目标在输入接受边沿 E 直接提交，只写 `lane_mask=1` 的 lane，不产生输出事务。
  紧邻的 E+1 输入可读取新 VACC，无需额外 bubble 或 forwarding。
- OUTPUT 目标不写 VACC；masked output lane 为 0。
- 所有 lane 被 mask 时，VACC 写入没有作用；OUTPUT 仍产生一个全零的有效向量。
- 没有有效 OUTPUT 请求时，若已有输出可前进，则清除 `out_valid`，`vec_out` 保持最后数值。
  仅在 `out_valid=1` 时输出数据具有事务意义。
- 复位边沿清零 VACC、`vec_out`、`out_valid`，取消未消费输出；`rst=1` 时 `in_ready=0`。
  因为复位同步，`out_valid` 的清零发生在复位边沿，外部握手在复位期间不计事务。

## Pooling、ReLU 与 clamp 映射

各 lane 对应 channel；四个向量来自同一 2×2 空间窗口，不进行 lane 间 reduction。
除需要屏蔽的尾部 channel 外，`lane_mask` 全 1：

| 操作 | `opcode` | `src_a_sel` | `src_b_sel` | `dst_sel` |
| --- | --- | --- | --- | --- |
| `VACC = v00` | MOV | VECTOR_A (`vec_a=v00`) | 任意 | DEST_VACC |
| `VACC = max(VACC,v01)` | MAX | VACC | VECTOR_B (`vec_b=v01`) | DEST_VACC |
| `VACC = max(VACC,v10)` | MAX | VACC | VECTOR_B (`vec_b=v10`) | DEST_VACC |
| `VACC = max(VACC,v11)` | MAX | VACC | VECTOR_B (`vec_b=v11`) | DEST_VACC |
| `output = VACC` | MOV | VACC | 任意 | OUTPUT |

MinPool 只需将上述三个 MAX 改为 MIN。初始化 MOV 的 mask 应覆盖该窗口后续需要读取的
channel，否则保留的 lane 仍是原来的 VACC 值。外部控制器负责窗口数据供应。

`MAX(vec_a, scalar=zero_point)` 实现含非零 zero point 的量化 ReLU。
Clamp 使用 `MAX(x, lower_bound) -> VACC`，再 `MIN(VACC, upper_bound) -> OUTPUT`。

## 实际仿真与复现

从仓库根目录执行（要求 PATH 中有 `iverilog`、`vvp`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_vector_unit_test.ps1
```

脚本遵循现有 Matrix Unit runner 的组织方式，用临时目录存放编译文件，打印实际
执行的编译与运行命令，失败返回非零，最后清理自己的临时目录。
8 lanes 运行 exhaustive 模式，1/3/16 lanes 运行定向和随机模式。
手动编译运行默认 8-lane 完整测试的命令为：

```powershell
iverilog -g2012 -Wall -s tb_vector_unit -o hardware/npu/sim/tb_vector_unit.vvp hardware/npu/rtl/vector_unit.sv hardware/npu/tb/tb_vector_unit.sv
vvp hardware/npu/sim/tb_vector_unit.vvp
```

`tb/tb_vector_unit.sv` 使用独立的 signed integer reference model，逐事务、逐 lane
自动比对，并同时检查寄存器结果出现时序、输出消费、VACC 更新及 stall 稳定性。
失败通过 `$fatal` 返回非零，数据错误报告 opcode、lane、A、B/scalar、expected、actual。
可定义 `DUMP_VCD` 生成 `vector_unit.vcd`。

覆盖范围：

- ADD/SUB 普通值与正负饱和，以及 ±128/127 比较极值；MOV 原样通过。
- 每个 opcode 的 vector-vector/vector-scalar，非零 zero point ReLU 和 clamp。
- 连续 VACC 依赖、逐 channel 2×2 MaxPool/MinPool 与独立四位置软件模型比对。
- partial/zero lane mask、masked VACC 保留、masked output 清零、保留 opcode。
- 48 个连续输出、input bubbles、强制和随机 output backpressure、blocked VACC 请求。
- 每种 lane 配置 2,000 个随机事务，混合所有 source/destination、opcode 和 lane mask。
- 默认 8 lanes 穷举 65,536 个 signed INT8 操作数对 × 5 opcode（327,680 lane 运算）。
- idle reset、阻塞输出期间 reset，以及复位后 VACC 读取与重新启动。

2026-09-26 本机 `iverilog -g2012 -Wall` 编译无警告，`vvp` 实际运行全部通过：

| LANES | DATA_WIDTH | accepted | consumed outputs | reset-cancelled outputs | stall cycles | input bubbles |
| --- | --- | --- | --- | --- | --- | --- |
| 8 | 8 | 43110 | 41721 | 1 | 198 | 658 |
| 1 | 8 | 2150 | 764 | 1 | 201 | 667 |
| 3 | 8 | 2150 | 787 | 1 | 244 | 628 |
| 16 | 8 | 2150 | 740 | 1 | 189 | 635 |

每个配置打印 `ALL VECTOR UNIT TESTS PASSED`；总脚本打印
`PASS vector unit regression: INT8, 8/1/3/16 lanes`。
accepted 包括只写 VACC 和保留 opcode，因此不等于输出数。
随机数使用 Icarus 默认种子；统计为上述本机实际运行记录。
原有 `run_matrix_unit_test.ps1` 也实际运行通过：PE 及 4×4、4×8、8×8
Matrix Unit 回归全部 PASS；其 RTL、testbench 与 runner 未修改。
当前完成 RTL 功能仿真；未进行 synthesis、timing 或硬件资源计数，不要求 VCS。
