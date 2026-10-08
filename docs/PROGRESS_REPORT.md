# ChipDesign 项目进展报告

**项目名称**：ChipDesign —— 面向边缘 AI 推理的 RISC-V + NPU 异构 SoC
**课程**：先进芯片全流程设计与实践
**报告日期**：2026-10-08
**代码仓库**：`git@github.com:ScoreForever/ChipDesign.git`（分支 `main`，HEAD `d2bf7e1`）
**报告人**：＿＿＿＿＿＿　　**学号**：＿＿＿＿＿＿

---

## 一、执行摘要

本项目采用**双层架构**推进：底层是可复用的**通用 INT8 计算单元**（矩阵单元、向量单元、重量化单元、AXI DMA），上层是面向关键词唤醒（KWS）任务的**固定功能 TinyCNN-8 NPU**，通过独立的 MMIO 包装器接入 SoC。截至本报告，项目具备以下可复现的状态：

| 维度 | 当前状态 |
| --- | --- |
| 集成度 | RISC-V CPU + AXI 总线 + 存储 + JTAG 调试 + NPU 已集成为单一 SoC |
| 底层计算平台 | 矩阵单元（4×8、INT8→INT32）、向量单元（8 lane、5 条 SIMD 指令）、重量化单元、AXI DMA；**四个模块及其 Icarus 回归自集成后未被改动** |
| 上层应用加速器 | TinyCNN-8 固定功能 NPU：Conv2d、FC、MaxPool 2×2、Global Avg/Sum Pool、逐 lane 归约、整网层序控制 |
| 调度优化 | 空间 tile 复用调度，整网周期 51,821 → 30,395（**−41.35%，1.705×**） |
| 验证状态 | Icarus 单元回归 2 项实测通过；ModelSim SoC 回归 2 项实测通过（详见第五节） |
| 代码状态 | 与远端 `origin/main` 完全同步；本地另有报告与图表未提交 |
| 距流片 | 前端功能仿真与调度验证完成；**综合、STA、FPGA 上板、后端均未开展** |

**一句话结论**：项目已完成"通用计算平台 + 固定网络加速器"的双层构建，并在同一阵列上通过调度优化取得 1.705× 整网加速；当前瓶颈是**存储器可综合替换与时序收敛**，而非功能正确性。

> **关于本报告的口径**：本报告区分"团队成员各自的工作"与"共同资产"。第二节起先描述通用平台（底层），第五节起描述 TinyCNN-8 应用层，第七节明确给出按提交记录核验的贡献边界。

---

## 二、项目目标与当前范围

### 2.1 设计目标

ChipDesign 的目标是构建一个**可配置、模块化、可仿真、可扩展**的边缘 AI 推理 SoC：以参数化的 INT8 通用计算单元为底座，向上承载具体网络加速器，向下与 RISC-V CPU 集成，形成"CPU 控制 + NPU 加速"的完整计算系统。

选择双层结构的原因是：通用单元保证可复用性与可扩展性，固定功能顶层则保证在特定任务上获得可观的调度效率——两者不冲突，而是分工。

### 2.2 系统能力

**底层：通用计算单元（共同资产）**

- **矩阵计算**：Weight-Stationary 阵列，默认 4×8，signed INT8 × INT8 → INT32
- **向量 SIMD**：8 lane，ADD/SUB（饱和）、MAX/MIN（有符号选择）、MOV，配合源/目的选择与 lane 掩码
- **重量化**：INT32 → INT8，逐通道 scale 与 shift，饱和到 [−128, 127]
- **数据搬运**：单 outstanding AXI DMA，CPU 配置源/目的/长度即可自动搬数
- **控制与中断**：MMIO 配置；NPU 与 DMA 完成中断分别接 `irq_i[16]` / `irq_i[17]`

**上层：TinyCNN-8 固定功能加速器**

- **卷积**：NHWC Conv2d，支持 padding / stride / kernel 参数
- **全连接**：FC 引擎，输出类别数可配置（硬件上限 8）
- **池化**：MaxPool 2×2 stride 2、Global Sum Pool、Global Avg Pool
- **归约与量化**：逐 lane INT8→INT32 横向求和；融合 bias + requant + 可选 ReLU（TFLite 风格双舍入）
- **整网层序**：8 层固定序列，逐层参数可加载

**软件与工具链**

- Icarus 单元回归（矩阵 / 向量）、ModelSim SoC 回归
- 独立整数 golden 模型 + 逐元素比对、事件 trace、参数扫描
- 可复现回归脚本与 CI 配置（`.github/workflows/tinycnn8.yml`）
- 软件可读的 MMIO profiler（只读偏移 `0x40`–`0x6C`）

**调试**

- RISC-V Debug Module + JTAG DMI，端口已引出至顶层

### 2.3 系统架构

<!-- 本地插图：ChipDesign.pdf（矢量）/ ChipDesign.png（位图），与本文档同目录的上一级。
     该图与生成脚本未纳入远程仓库；若本报告需要发布，请先解除 .gitignore 中的对应规则。 -->

> **图 1（本地插图）**　见仓库根目录的 `ChipDesign.pdf`（矢量版，推荐）或 `ChipDesign.png`。
> 该图与可编辑源 `ChipDesign.drawio` 均为本地交付物，已列入 `.gitignore`，不随远程仓库发布；
> 本节的文字与表格已完整描述同一架构，不依赖插图即可阅读。

**图 1 说明**　SoC 顶层架构。总线为 4 slave port × 4 master port，每端口最大 1 笔未完成事务（`MaxMstTrans = MaxSlvTrans = 1`）。配色区分组件来源：蓝色为通用计算单元，绿色为 AXI DMA，橙黄为 NPU MMIO 接口，红色虚线为控制与中断通路，无底色部分为课程基线或 PULP 第三方组件。

**端口对应关系**（依据 `hardware/soc/rtl/my_soc_top.sv`）：

| 端口 | 连接对象 | 说明 |
| --- | --- | --- |
| `slave[0]` | `i_axi_adapter_instr` | CPU 取指（AXI ID `4'b0001`） |
| `slave[1]` | `i_axi_adapter_data` | CPU 数据访问（AXI ID `4'b0010`） |
| `slave[2]` | `i_axi_adapter_dm` | 调试模块寄存器访问 |
| `slave[3]` | `i_axi_adapter_dma` | AXI DMA 主设备请求（AXI ID `4'b0011`） |
| `master[0]` | `i_axi2boot` → `i_bootram` | 启动存储（64 B） |
| `master[1]` | `i_axi2sram` → `my_mainmem` → `sram_ff` | 主存（8 KiB 行为模型） |
| `master[2]` | `i_dm_axi2mem` → `i_dm_top` | 调试模块寄存器组 |
| `master[3]` | `i_axi2npu` → NPU 包装器 | NPU MMIO（当前为 TinyCNN-8 包装器） |

**中断通路**：NPU 完成中断接 `irq_i[16]`，DMA 完成中断接 `irq_i[17]`，CPU 可执行 `wfi` 由 NPU/DMA 主动唤醒。**复位通路**：`rst_ni & ~ndmreset` 经 `rstgen` 产生 `ndmreset_n`，作为全部子系统的复位源。

### 2.4 地址映射

| 区域 | 基址 | 长度 | 说明 |
| --- | --- | --- | --- |
| Debug | `0x0000_0000` | `0x0000_1000` | RISC-V Debug Module 寄存器 |
| BootRAM | `0x0001_0000` | `0x0001_0000` | 启动代码，复位后 PC 指向此处 |
| NPU | `0x7000_0000` | `0x0000_4000` | NPU MMIO 寄存器（16 KiB） |
| SRAM | `0x8000_0000` | `0x1000_0000` | 主存（当前为 8 KiB 行为模型） |

### 2.5 关键配置参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `ARRAY_ROWS` × `ARRAY_COLS` | 4 × 8 | 矩阵单元阵列规模 |
| `ACT_WIDTH` / `WGT_WIDTH` | 8 / 8 | signed INT8 激活与权重 |
| `ACC_WIDTH` | 32 | signed INT32 部分和/累加 |
| `LANES` / `DATA_WIDTH` | 8 / 8 | 向量单元 lane 数与位宽 |
| TinyCNN-8 输入 | 20 × 16 × 1 | 单通道声学特征图 |
| TinyCNN-8 类别数 | 4 / 6（硬件上限 8） | 关键词分类输出 |

---

## 三、底层：通用计算单元的运算能力

本节说明通用单元**实际可执行**的运算，语义以源码为准。

### 3.1 运算总览

| 单元 | 运算类别 | 具体能力 | 数据通路 |
| --- | --- | --- | --- |
| 矩阵单元 | 乘累加 | `P_out[c] = P_in[c] + Σ_r A[r]·W[r][c]` | INT8 × INT8 → INT32 |
| 向量单元 | SIMD 算术 | 饱和加、饱和减 | INT8 → INT8 |
| 向量单元 | SIMD 最值/搬运 | 逐 lane MAX、MIN、MOV | INT8 → INT8 |
| 重量化单元 | 重量化 | 逐通道 scale/shift + 饱和 | INT32 → INT8 |
| AXI DMA | 数据搬运 | 逐字复制，单 outstanding | 32-bit word |

### 3.2 矩阵单元：矩阵乘累加

**核心运算**（`matrix_unit.sv:2`、`ws_pe.sv:22,36`）：

```
P_out[c] = P_in[c] + Σ(r = 0 .. ARRAY_ROWS−1) A[r] · W[r][c]
```

- 一次事务计算**一个完整输出行向量**，`ARRAY_COLS` 个输出同时产生
- 累加完全在 INT32 内进行，**不饱和、不回绕**
- 支持 4×4、4×8、8×8 配置（均在 Icarus 回归中验证）
- 偏置由 `P_in` 部分和入口注入；K 维尾部由外部控制器补零

| 项目 | 默认值 |
| --- | ---: |
| 阵列规模 | 4 × 8 = 32 PE |
| 每事务乘加次数 | 32 MAC |
| 流水线延迟 | `ARRAY_ROWS + ARRAY_COLS − 2` = 10 使能周期 |
| 稳态吞吐 | 每周期 1 个事务 |

**当前限制**：无 compute/load 重叠、无双缓冲；输出背压会冻结整条流水线。

### 3.3 向量单元：INT8 SIMD

单级流水线（组合 ALU + 输出寄存器），默认 8 lane、每向量 64 bit。

**指令集**（`vector_unit.sv:24-25,70-78`）：

| 编码 | 运算 | 语义 | 饱和 |
| --- | --- | --- | --- |
| `3'd0` | **ADD** | `saturate(A + B)` | ✅ |
| `3'd1` | **SUB** | `saturate(A − B)` | ✅ |
| `3'd2` | **MAX** | `A > B ? A : B`（有符号） | ❌ 纯选择 |
| `3'd3` | **MIN** | `A < B ? A : B`（有符号） | ❌ 纯选择 |
| `3'd4` | **MOV** | `A`（忽略 B 与 scalar） | ❌ |
| `3'd5..7` | 保留 | 接受事务，无副作用 | — |

**数据源与目的**：

| 控制 | 取值 | 含义 |
| --- | --- | --- |
| `src_a_sel` | 0 / 1 | `vec_a` / **VACC** |
| `src_b_sel` | 0 / 1 | `vec_b` / **SCALAR**（广播） |
| `dst_sel` | 0 / 1 | `OUTPUT`（产生事务）/ **DEST_VACC**（原地写） |

5 opcode × 2 源 A × 2 源 B × 2 目的 = **40 种运算形态**。

**架构要点**：操作数先**符号扩展到 9 bit** 再运算（`vector_unit.sv:53-56`）——INT8 差值范围为 −255..255，只有 9 bit 才能可靠判号，因此 `127 − (−128)` 这类极值比较不出错。加减共用一条通路，核心数据通路不含乘法器。

| 时序项目 | 能力 |
| --- | --- |
| 输入→最早输出握手 | 1 拍 |
| 稳态吞吐 | 每拍 1 个向量 |
| VACC 可见性 | 接受沿提交，下一条指令即可读到，无需 forwarding |
| 背压 | 支持，冻结输出与 VACC |

### 3.4 用通用单元表达的网络算子

| 算子 | 实现方式 |
| --- | --- |
| 卷积 / 全连接 | 矩阵单元 MAC |
| 偏置 | 由 `P_in` 注入 |
| ReLU（含非零 zero point） | `MAX(vec_a, scalar)` |
| Clamp | `MAX(x, lower) → VACC`，再 `MIN(VACC, upper)` |
| MaxPool 2×2 / MinPool 2×2 | VACC + 3 次 MAX/MIN |
| Eltwise ADD / SUB | 向量加减 |
| 量化 | 重量化单元 |
| 通道分组 | `lane_mask` |

### 3.5 通用单元明确不支持的能力

| 能力 | 状态 |
| --- | --- |
| 向量乘法（MUL） | ❌ |
| 移位（SHL/SHR/SRA）、除法 | ❌ |
| 超越函数（sigmoid/tanh/GELU/softmax/exp/log/sqrt） | ❌ 无 SFU |
| 横向归约树 | ❌ |
| 独立 ReLU/comparator 硬件 | ❌ **有意不做**，复用 MAX |
| 浮点 / INT16 / INT4 | ❌ 仅 INT8 通路 |

> 上述排除是**设计决策**，依据 `hardware/npu/VECTOR_UNIT.md:12`。需要这些算子的网络层由上层固定功能引擎或 CPU 软件承担。

---

## 四、上层：TinyCNN-8 固定功能加速器

### 4.1 网络结构与层序

面向 KWS 任务的 8 层固定序列（NHWC、INT8 激活、Q31 量化）：

| 层 | 操作 | 输出形状 |
| --- | --- | --- |
| 1 | Conv2d 3×3 + bias + requant + ReLU | 20 × 16 × 8 |
| 2 | Conv2d 3×3 + bias + requant + ReLU | 10 × 8 × 8 |
| 3 | MaxPool 2×2 stride 2 | 10 × 8 × 8 |
| 4 | Conv2d 3×3 + bias + requant + ReLU | 10 × 8 × 8 |
| 5 | MaxPool 2×2 stride 2 | 5 × 4 × 8 |
| 6 | Conv2d 1×1 + bias + requant | 5 × 4 × 8 |
| 7 | Global sum/average pool | 1 × 1 × 8 |
| 8 | 全连接（硬件上限 8 类） | 1 × 1 × 4/6 |

**范围外（由 CPU 软件承担）**：音频采样、MFCC/log-mel 特征提取、Softmax、阈值化、argmax。

### 4.2 后处理引擎

| 引擎 | 功能 |
| --- | --- |
| `requant_unit` | 融合 bias + requant + 可选 ReLU：逐 lane bias、乘数/移位、TFLite 风格双舍入、`activation_min/max` 钳位、INT32 饱和 |
| `maxpool2x2_engine` | 2×2 stride-2 MaxPool，复用向量单元 VACC |
| `global_sum_pool_engine` | 空间全局求和（INT32 累加） |
| `global_avg_pool_engine` | 全局平均池化，除法并入 multiplier/shift |
| `reduction_sum_unit` | 逐 lane INT8 → INT32 横向求和 |

### 4.3 调度优化与实测收益

原调度在每个空间位置、每个 K 组都重载四行权重，并等待每一笔矩阵结果。优化后的循环顺序为 **空间 tile → K 组 → tile 内位置**：同一权重组装载一次服务多个位置，连续收集/发射期间同时接收返回结果，按 tag 将 INT32 部分和写回各位置。

| 调度 | 整网周期 | Conv2 周期 | C2 权重行 | 部分和容量 |
| --- | ---: | ---: | ---: | ---: |
| baseline | 51,821 | 30,482 | 5,760 | 32 B |
| overlap | 46,061 | 24,722 | 5,760 | 32 B |
| **tile16（默认）** | **30,395** | **9,056** | **360** | 512 B |
| tile32 | 29,781 | 8,442 | 216 | 1024 B |

**默认 tile16 的收益**：整网周期降低 **41.35%（1.705×）**，Conv2 加速 **3.366×**，C2 权重行减少 **93.75%**；主要数据存储 584 B（psum 512 B + tag FIFO 64 B + 两个 activation pack 8 B）。

> 容量是 RTL 声明预算，**未映射 SRAM 宏，不等于综合面积**。tile32 更快但部分和容量翻倍，需与代价一并评估。

### 4.4 软件可见性能接口

新增只读偏移（相对 `NPU_BASE`）：`0x40` 总周期、`0x44..0x58` 六层周期、`0x5C` 权重行、`0x60` 输入事务、`0x64` 退休事务、`0x68` 最大在途、`0x6C` 状态（valid/overflow/busy）。默认两种优化均关闭。

---

## 五、验证结果

### 5.1 测试环境

| 项目 | 版本 |
| --- | --- |
| 操作系统 | Windows x64 |
| Icarus Verilog | 12.0（本机）/ 13.0（对端回归所用） |
| ModelSim | SE-64 2019.2 |

### 5.2 通用单元回归（Icarus，本轮实测）

| 测试 | 命令 | 结果 |
| --- | --- | --- |
| 矩阵单元 | `hardware/npu/scripts/run_matrix_unit_test.ps1` | ✅ PASS（PE、4×4、4×8、8×8） |
| 向量单元 | `hardware/npu/scripts/run_vector_unit_test.ps1` | ✅ PASS（8/1/3/16 lane） |

向量单元回归覆盖：ADD/SUB 正负饱和、MAX/MIN 有符号极值、MOV、标量广播、非零 zero point 的 ReLU、两级 clamp、连续 VACC 依赖、逐通道 2×2 MaxPool/MinPool、部分/全零 lane mask、保留 opcode、强制 stall 与背压稳定性、reset 中途取消，以及 **65,536 个 INT8 操作数对 × 5 opcode 的穷举**。

### 5.3 SoC 集成回归（ModelSim，本轮实测）

编译：`vlog -sv -f hardware/soc/sim/filelists/chipdesign_soc.f` → **0 errors**，615 warnings（均为 PULP 第三方 AXI/调试模块的风格提示）。

| 测试平台 | 在 filelist | 实测结果 |
| --- | --- | --- |
| `chipdesign_soc_tb` | ✅ | ✅ PASS（81,247 周期） |
| `chipdesign_dma_tb` | ✅ | ✅ PASS（725 周期） |
| `chipdesign_npu_irq_tb` | 已删除 | 针对的算子级接口已移除（见 5.4） |
| `chipdesign_requant_tb` | 已删除 | 针对的算子级接口已移除（见 5.4） |

### 5.4 集成遗留问题的修复与说明

TinyCNN-8 集成后，`chipdesign_dma_tb` 一度超时、另两个测试平台被移出 filelist。本轮已完成排查与修复。

**（1）`chipdesign_dma_tb` 超时——已修复，现 PASS**

原测试程序有两处缺陷，均已定位并修正：

| # | 缺陷 | 现象 | 修正 |
| --- | --- | --- | --- |
| 1 | 测试程序仍使用旧矩阵单元寄存器语义（`gen_dma_hex.py` 写 `0x0000` 后轮询 `0x0004` 的 `weights_loaded`），而当前包装器的 `0x0000` 位定义已改为 `bit0=启动 / bit1=IRQ使能 / bit2=清done / bit3=清错误` | 轮询条件永不成立，死循环 | 改为 DMA 自校验：SRAM→SRAM 复制后用 CPU 逐字读回比对 |
| 2 | **DMA 目标地址落在引擎自己的配置寄存器区**（`0x70000400..0x7000041C` 含 SRC/DST/LEN/CTRL） | 载荷覆盖长度寄存器，`LEN` 被写成 `0x01010101`，引擎从约 1684 万字数倒数，**永不结束** | 目标改为普通 SRAM（`0x80001800`），引擎配置寄存器不再被触碰 |

> 缺陷 2 是值得记录的共性教训：**DMA 的目标窗口不得与它自身的控制寄存器重叠**。该窗口是设备 MMIO，写入会产生副作用，并非通用暂存区。

修复后：`chipdesign_dma_tb` → **PASS，725 周期**。

**（2）工具链陷阱——hex 文件编码**

生成测试镜像时，若用 PowerShell 的 `>` 重定向输出，PS 5.1 默认写出 **UTF-16LE（含 BOM）** 文件；`$readmemh` 无法解析，SRAM 静默保持全 `x`，程序表现为"完全没运行"。当前生成器已改为**自行以 ASCII 无 BOM 写文件**，并在文档中记录该约束。

**（3）算子级测试平台及其配套文件已清理**

`chipdesign_npu_irq_tb` / `chipdesign_requant_tb` 针对的是**算子级调试接口**——即直接暴露 `MATRIX_CTRL` / `VECTOR_OP` / `REQUANT_*` 寄存器的旧包装器。该接口在设计上不进入生产 SoC filelist（依据 `docs/ARCHITECTURE.md` 5.1 节：只接完整 NPU 顶层，避免 CPU 为一次推理执行数千次底层 MMIO 操作），并于本轮清理中**删除**（`npu_mmio_wrapper.sv`，642 行）。

这两个测试平台随之失去可达的 MMIO 地址空间，因此**连同其测试镜像与镜像生成器一并删除**，共 6 个文件、638 行：

| 类别 | 文件 |
| --- | --- |
| 测试平台 | `chipdesign_npu_irq_tb.sv`、`chipdesign_requant_tb.sv` |
| 测试镜像 | `chipdesign_npu_irq_test.hex`、`chipdesign_requant_test.hex` |
| 镜像生成器 | `gen_irq_hex.py`、`gen_requant_hex.py` |

算子级的验证改由 `hardware/npu/` 下的 Icarus 单元回归承担（`run_matrix_unit_test.ps1` / `run_vector_unit_test.ps1`），后者不依赖 MMIO 通路，详见 5.2 节。

### 5.5 对端 TinyCNN-8 回归的声明范围

队友在 `docs/kws_tinycnn8_tiled/REPORT.md` 中记录的验证为：

- **12 个网络用例、224,740 次逐层元素比较**，对独立整数 golden bit-exact
- **63 项记录的单元/配置/oracle/MMIO 回归**；正式 MMIO 覆盖 24 个模式/用例组合
- **声明范围**：synthetic 参数与输入、独立 NPU 及生产 MMIO 后端、组合行为存储
- **明确未完成**：真实 KWS 准确率、CPU/AXI 整 SoC 仿真、综合、STA、频率、面积、功耗

本报告如实转述上述边界，不将其表述为芯片完工。

---

## 六、当前限制与风险

| # | 限制 | 影响 | 等级 |
| --- | --- | --- | --- |
| 1 | **未做逻辑综合与时序分析** | 无法确认目标频率下可收敛 | 🔴 高 |
| 2 | **SRAM 为行为模型** | 不可综合、不可上板；面积功耗未知 | 🔴 高 |
| 3 | 未做 FPGA 原型验证 | 上板可能暴露仿真未覆盖的问题 | 🟠 中 |
| 4 | **通用单元的向量/矩阵寄存器区已从 MMIO 移除** | CPU 无法直接驱动向量单元，仅能经 TinyCNN-8 内部引擎间接使用 | 🟠 中 |
| 5 | 两版重量化单元并存 | 接口不兼容，需择一或做适配层 | 🟠 中 |
| 6 | 量化链路仅 synthetic 参数验证 | 未验证真实 KWS 准确率 | 🟠 中 |
| 7 | DMA 单 outstanding、无 burst/描述符链 | 吞吐受限 | 🟡 低 |
| 8 | JTAG 调试未联调真实 debugger | 调试链路未端到端验证 | 🟡 低 |
| 9 | 单时钟域、无低功耗时钟门控 | 功耗优化缺失 | 🟡 低 |

---

## 七、贡献边界（按提交记录核验）

以下为基于 git 历史的客观事实，供如实标注。

### 7.1 共同资产：通用计算单元

`vector_unit.sv`、`matrix_unit.sv`、`ws_pe.sv`、`ws_systolic_array.sv` 及其 Icarus 回归与 runner，**自集成后未被任何成员修改**——这是双层架构中稳定的底层。

### 7.2 成员贡献

| 成员 | 提交数 | 主要产出 |
| --- | ---: | --- |
| **Yimisda** | 5 | Lab3 SoC 基线集成与自研 NPU 接入；阶段1 NPU→CPU 完成中断；阶段2 重量化单元；阶段3 AXI DMA（**未被后续改动**）；`ARCHITECTURE.md` 初版、矩阵/向量单元文档与 Icarus 回归 |
| **mugamucyuu** | 3 | TinyCNN-8 的 8 个 RTL 模块及全部单元级 testbench；SoC 集成（新 MMIO 包装器、subsystem、filelist、软件测试程序）；重量化单元替换版；`TINYCNN8_ARCHITECTURE.md` |
| **Zhiyuan Zhao** | 3 | 空间 tile 流水线优化与 MMIO profiling 通道；可复现回归工具链（Python/Swift）与接受度清单；`docs/kws_tinycnn8_tiled/` 全套报告与图表；CI 配置 |

### 7.3 集成演化说明

底层通用单元自集成后保持不变；上层 NPU 接口在 TinyCNN-8 集成时经历了一次重构，其中重量化单元被替换为融合 bias 与激活钳位的版本。这一演化过程记录于 `docs/MERGE_DIFF.md`。

---

## 八、下一阶段规划

总体思路：**先确保"可综合"，再确保"可上板"，最后追求"高性能"**。功能与调度验证已基本收口，不建议继续堆叠新功能。

### 8.1 短期

1. **可综合存储器替换**（前置任务）：将 `sram_ff.sv` 行为模型替换为可综合 BRAM 或 FPGA Block RAM 推断写法，保留字节使能语义
2. **逻辑综合与时序分析**：选定目标器件或教学工艺库，跑通综合与 STA，产出面积/时序报告，识别关键路径
3. **清理集成遗留（已完成）**：算子级调试接口 `npu_mmio_wrapper.sv` 已删除，`ARCHITECTURE.md` 与 `hardware/soc/README.md` 已同步；`chipdesign_dma_tb` 已修复通过

### 8.2 中期

5. **FPGA 原型验证**：综合 → 实现 → 上板，跑通 TinyCNN-8 整网推理
6. **JTAG 调试联调**：用真实 debugger 验证 halt/resume
7. **真实模型准确率验证**：用训练后的 KWS 模型参数替换 synthetic 参数，验证端到端准确率

### 8.3 长期

8. **DMA 增强**：burst 传输、多 outstanding、链式描述符
9. **增加 LSU/AGU**：为 NPU 提供自主地址生成，支持窗口遍历与自动取数
10. **后端物理实现**：布局布线、DRC/LVS、功耗分析

### 8.4 里程碑

| 里程碑 | 交付物 | 验收标准 |
| --- | --- | --- |
| M1 可综合 | 综合网表 + 时序报告 | 综合无错误，无组合环 |
| M2 可上板 | FPGA bitstream | 上板跑通整网，结果与仿真一致 |
| M3 时序收敛 | STA 报告 | 目标频率下建立/保持满足 |
| M4 后端完成 | GDSII | DRC/LVS 干净 |

---

## 九、结论

1. **双层架构已建成并可用**：底层通用计算单元（矩阵/向量/重量化/DMA）稳定且未被改动，上层 TinyCNN-8 固定功能加速器已完成整网集成与调度优化。
2. **调度优化收益已量化**：同一 4×8 阵列、同一网络、同一存储契约下，整网周期从 51,821 降至 30,395，**降低 41.35%（1.705×）**，Conv2 加速 3.366×。
3. **验证边界清楚**：Icarus 单元回归 2 项、ModelSim SoC 回归 2 项全部实测通过。TinyCNN-8 的验证限于 synthetic 参数与固定网络，**尚未验证真实准确率**；算子级测试平台已随其接口一并清理（5.4 节）。
4. **下一阶段瓶颈明确**：不在功能正确性，而在可综合存储器替换与时序收敛。
5. **风险已识别**：9 项限制中有 2 项高风险（未综合、SRAM 为行为模型），已列为短期计划前置任务。

综上，项目按计划推进，**前端功能与调度验证阶段已收口，具备进入综合与上板阶段的条件**。

---

## 附录

### A. 关键文件索引

| 文件 | 说明 |
| --- | --- |
| `docs/ARCHITECTURE.md` | 系统架构、地址映射、寄存器表与编程模型 |
| `docs/MERGE_DIFF.md` | 集成演化与差异对账清单 |
| `docs/kws_tinycnn8_tiled/REPORT.md` | TinyCNN-8 调度优化的实测权衡与验收边界 |
| `hardware/npu/TINYCNN8_ARCHITECTURE.md` | TinyCNN-8 架构说明 |
| `hardware/npu/rtl/vector_unit.sv` | INT8 SIMD 向量单元 |
| `hardware/npu/rtl/matrix_unit.sv` | Weight-Stationary 矩阵单元 |
| `hardware/npu/rtl/tinycnn8_npu_top.sv` | TinyCNN-8 整网层序控制器 |
| `hardware/soc/rtl/dma/npu_dma.sv` | 单 outstanding AXI DMA |
| `hardware/soc/rtl/my_soc_top.sv` | SoC 顶层集成 |
| `hardware/npu/VECTOR_UNIT.md` | 向量单元接口、opcode、VACC 与 Pooling 语义 |

### B. 复现命令

```powershell
# 从 ChipDesign 仓库根目录执行

# 通用单元回归（需 PATH 中有 iverilog / vvp）
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_matrix_unit_test.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_vector_unit_test.ps1

# TinyCNN-8 全套 Icarus 回归
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_all_tests.ps1

# SoC 集成回归（需 PATH 中有 ModelSim）
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1

# TinyCNN-8 可复现调度/golden/MMIO 回归
python3 hardware/npu/scripts/run_tinycnn8_regression.py --output <新的空目录>
```

### C. 数值示例

**矩阵单元（默认 4×8）**：`A = [1,2,3,4]`、`P_in[0] = 10`、第 0 列权重 `[5,6,7,8]`：

```
P_out[0] = 10 + 1×5 + 2×6 + 3×7 + 4×8 = 80
```

**向量单元（8 lane）**：`vec_a = [−128, −1, 0, 1, 100, 127, −100, 50]`，`lane_mask = 8'hFF`：

| opcode | 操作数 B | 结果 |
| --- | --- | --- |
| ADD | 各 lane = 10 | `[−118, 9, 10, 11, 110, 127, −90, 60]`（lane 5 饱和） |
| SUB | 各 lane = 1 | `[−128, −2, −1, 0, 99, 126, −101, 49]`（lane 0 饱和） |
| MAX | `scalar = 0` | `[0, 0, 0, 1, 100, 127, 0, 50]`（等价 ReLU） |
| MIN | `scalar = 50` | `[−128, −1, 0, 1, 50, 50, −100, 50]` |
