# ChipDesign 合并差异清单（对账用）

**生成时间**：2026-10-07
**最后更新**：2026-10-08（已修复 DMA 测试、清理两处死代码，见第三、五、七节）
**比对区间**：`9ca44a0`（Yimisda，阶段3）→ `d2bf7e1`（origin/main，Merge PR #4）
**规模**：7 笔提交、70 个文件、+8168 / −943 行

> 用途：与队友核对各方改动归属与接口影响，再决定进展报告的表述口径。
> 本清单只陈述事实与可验证证据，不含评价。

---

## 一、提交归属

| 提交 | 作者 | 时间 | 文件数 | 内容 |
| --- | --- | --- | ---: | --- |
| `e663a41` | mugamucyuu `<cmy1008@stu.pku.edu.cn>` | 09-30 23:22 | 30 | TinyCNN-8 加速器实现与验证 |
| `85c0b47` | mugamucyuu | 10-08 00:15 | 4 | 合并 tinycnn8-npu 分支 |
| `46d759b` | mugamucyuu | 10-08 01:08 | 22 | 把完整 TinyCNN-8 NPU 集成进 SoC |
| `51477df` | Zhiyuan Zhao `<2400012709@stu.pku.edu.cn>` | 10-07 14:06 | 20 | 空间 tile 流水线 + MMIO profiling |
| `3a95eaf` | Zhiyuan Zhao | 10-07 14:14 | 1 | 修正可复现性清单中的路径 |
| `b910290` | Zhiyuan Zhao | 10-07 14:22 | 13 | 记录实测权衡与综合前验收 |
| `d2bf7e1` | Zhao Zhiyuan | 10-07 22:13 | 0 | Merge PR #4 |

**区间前的既有提交（Yimisda，即 9ca44a0 及以前）**：SoC 集成、架构文档、阶段1 中断、阶段2 Requant、阶段3 DMA。

---

## 二、新增模块（全部由队友新增，共 8 个 RTL 文件）

| 文件 | 行数 | 功能 |
| --- | ---: | --- |
| `hardware/npu/rtl/conv2d_engine.sv` | 560 | NHWC Conv2d，含 padding/stride/kernel 参数 |
| `hardware/npu/rtl/conv_window_addr_gen.sv` | 172 | 卷积窗口地址生成 |
| `hardware/npu/rtl/fc_engine.sv` | 126 | 全连接层 |
| `hardware/npu/rtl/maxpool2x2_engine.sv` | 169 | 2×2 stride-2 MaxPool 控制器 |
| `hardware/npu/rtl/global_sum_pool_engine.sv` | 116 | 全局求和池化 |
| `hardware/npu/rtl/global_avg_pool_engine.sv` | 89 | 全局平均池化（除法并入 multiplier/shift） |
| `hardware/npu/rtl/reduction_sum_unit.sv` | 63 | 逐 lane INT8→INT32 横向求和 |
| `hardware/npu/rtl/tinycnn8_npu_top.sv` | 261 | 整网层序控制器 |
| `hardware/soc/rtl/npu/tinycnn8_npu_mmio_wrapper.sv` | 372 | SoC 侧的新 MMIO 包装器 |

---

## 三、被覆盖的既有文件（需要确认归属）

| 文件 | 我方版本 | 上游改动 | 谁改的 |
| --- | --- | --- | --- |
| `hardware/npu/rtl/requant_unit.sv` | 63 行（Yimisda，`76a4b31`） | **+156 / −50**，变为 206 行 | mugamucyuu（`e663a41`、`46d759b`） |
| `hardware/soc/rtl/npu/npu_mmio_wrapper.sv` | 我方 SoC 主包装器 | 先 **+69 / −46** 同步到新 requant 接口，随后**整体删除** | mugamucyuu（`85c0b47`、`46d759b`）+ Yimisda（删除） |
| `hardware/soc/rtl/my_npu_subsystem.sv` | 例化 `npu_mmio_wrapper` | 改为例化 `tinycnn8_npu_mmio_wrapper` | mugamucyuu + Zhiyuan Zhao |
| `hardware/soc/rtl/my_soc_top.sv` | 注释"Matrix Unit + Vector Unit" | 1 行注释改为"Complete fixed-function TinyCNN-8 NPU" | mugamucyuu |
| `hardware/soc/sim/filelists/chipdesign_soc.f` | 含 4 个 SoC 测试平台 | **+8 / −3** | mugamucyuu |
| `docs/ARCHITECTURE.md` | 我方系统架构规格 | **747 行重写** | mugamucyuu + Zhiyuan Zhao |
| `hardware/soc/sim/sw/gen_hex.py` | 我方玩具汇编器 | 重写 | mugamucyuu |
| `hardware/soc/sim/sw/chipdesign_npu_test.S/.hex` | 我方 NPU 测试程序 | 重写 | mugamucyuu |
| `hardware/soc/sim/tb/chipdesign_soc_tb.sv` | 我方集成测试 | +12 | mugamucyuu |

### 3.1 `requant_unit.sv` 的两版对比

| | 我方（`76a4b31`） | 上游（`46d759b`） |
| --- | --- | --- |
| 行数 | 63 | 206 |
| 接口 | `clk_i`/`rst_ni`（低有效）、`valid_i`/`valid_o` | `clk`/`rst`（高有效）、`in_valid`/`in_ready`/`out_valid`/`out_ready` |
| 量化公式 | `(acc × scale) >>> shift + zp` | 乘数+移位、TFLite 风格**双舍入** |
| 额外能力 | 逐通道 INT16 scale | 逐 lane bias、`lane_mask`、`activation_min/max` 激活钳位、INT32 饱和 |

**这是两套接口不兼容的实现，同一路径、同名模块，不可共存。**

---

## 四、未被上游改动的我方工作（仍然有效）

| 文件 | 状态 |
| --- | --- |
| `hardware/npu/rtl/vector_unit.sv` | ✅ **一行未改** |
| `hardware/npu/rtl/matrix_unit.sv` | ✅ 一行未改 |
| `hardware/npu/rtl/ws_pe.sv` | ✅ 一行未改 |
| `hardware/npu/rtl/ws_systolic_array.sv` | ✅ 一行未改 |
| `hardware/npu/VECTOR_UNIT.md` | ✅ 未改 |
| `hardware/npu/tb/tb_vector_unit.sv`、`tb_matrix_unit.sv`、`tb_ws_pe.sv` | ✅ 未改 |
| `hardware/npu/scripts/run_vector_unit_test.ps1`、`run_matrix_unit_test.ps1` | ✅ 未改 |
| `hardware/soc/rtl/dma/npu_dma.sv` | ✅ 未改（仍在 filelist 中） |
| `hardware/soc/sim/tb/chipdesign_npu_irq_tb.sv`、`chipdesign_requant_tb.sv` | 已从 filelist 移除，随后**连同镜像与生成器一并删除**（`6 文件 / 638 行`） |

结论：**基础计算单元（Matrix / Vector / DMA）的所有权清晰，未被触碰**；被替换的是它们之上的集成层与 requant。

---

## 五、MMIO 寄存器映射的变化（关键影响）

### 5.1 我方版本保留的寄存器区块

```
0x0000 MATRIX_CTRL          0x0200 MATRIX_OUT[0..7]
0x0004 MATRIX_STATUS        0x0300 REQUANT_CTRL
0x0008 VECTOR_CTRL          0x0304 REQUANT_STATUS
0x000C VECTOR_LANE_MASK     0x0310 REQUANT_SCALE[0..7]
0x0010 VECTOR_SCALAR        0x0330 REQUANT_SHIFT
0x0014 VECTOR_STATUS        0x0334 REQUANT_ZERO_POINT
0x0018 VECTOR_OP            0x0340 REQUANT_OUT_LO/HI
0x0020 VECTOR_SRC_A_LO/HI   0x0400..0x0410 DMA_*
0x0028 VECTOR_SRC_B_LO/HI
0x0030 VECTOR_OUT_LO/HI
0x0040 MATRIX_WEIGHT[0..7]
0x0100 MATRIX_ACT
0x0110 MATRIX_PSUM[0..7]
```

### 5.2 上游新版本的寄存器区块

```
0x0000, 0x0004, 0x0008, 0x000C, 0x0010    控制与状态
0x002C, 0x0030                             
0x0040, 0x0044, 0x0058 .. 0x006C          
0x0400..0x0410                            DMA_*（与旧版一致 ✅）
0x1000..(INPUT_BYTES*4)                   输入张量
0x2000..(WEIGHT_WORDS*8)                  权重
0x3000..0x33FF                            逐层参数
```

### 5.3 结论

| 区块 | 状态 |
| --- | --- |
| **`0x0400..0x0410` DMA 寄存器** | ✅ **两版完全一致，DMA 通路未变** |
| **`0x0008..0x0034` VECTOR_\* 整套** | ❌ **已移除** |
| **`0x0100 / 0x0110 / 0x0200` MATRIX_ACT / PSUM / OUT** | ❌ 已移除 |
| **`0x0300..0x0344` REQUANT_\* 整套** | ❌ 已移除 |
| **`0x0000` 位定义** | ⚠️ **语义已变**：旧为 `bit0=start compute, bit1=load weights`；新为 `bit0=启动 NPU, bit1=IRQ 使能, bit2=清 done, bit3=清错误` |

**需要共同确认的问题**：`vector_unit` 硬件仍在 RTL 中且未被修改，但**已无 MMIO 通路可达**，即 CPU 无法再直接驱动向量单元，只能由 TinyCNN-8 内部引擎间接使用。请确认这是有意设计还是集成疏漏。

---

## 六、测试回归的现状（我方实测）

| 测试平台 | filelist | 实测结果 |
| --- | --- | --- |
| `chipdesign_soc_tb` | ✅ 在 | ✅ PASS（**81247 周期**） |
| `chipdesign_dma_tb` | ✅ 在 | ✅ PASS（**725 周期**，超时缺陷已修复，见 6.1） |
| `chipdesign_npu_irq_tb` | 已删除 | 针对的算子级接口已移除 |
| `chipdesign_requant_tb` | 已删除 | 针对的算子级接口已移除 |
| Icarus：`tb_vector_unit` | ✅ 未改 | ✅ PASS（8/1/3/16 lane） |
| Icarus：`tb_matrix_unit` | ✅ 未改 | ✅ PASS（PE、4×4、4×8、8×8） |

**编译**：`vlog -sv -f chipdesign_soc.f` → 0 错误、615 警告。

### 6.1 DMA 测试超时：根因与修复（已完成）

原测试程序有两处独立缺陷；DMA RTL 本身无问题。

**缺陷 1 — 旧寄存器语义。** 程序写 `0x0000` 后轮询 `0x0004` 的 `weights_loaded`。
当前包装器的 `0x0000` 位定义为 `bit0=启动 / bit1=IRQ使能 / bit2=清done / bit3=清错误`，
故轮询条件永不成立。

**缺陷 2 — DMA 目标落在引擎自己的配置寄存器上。** 中间修复曾把目标设为
`0x70000400..0x7000041C`，该窗口含 DMA 的 SRC/DST/LEN/CTRL。载荷覆盖 `LEN` 为
`0x01010101`，引擎从约 1684 万字数开始倒数，**永不结束**。实测计数器轨迹：

```text
dst=0x70000408  cnt=0x00000006    <- 正常
dst=0x01010101  cnt=0x01010101    <- 计数器被载荷覆盖
dst=0x01010105  cnt=0x01010100    <- 从 0x01010101 递减
```

**修复**：改为 SRAM→SRAM 自校验（`0x80001000` → `0x80001800`），引擎配置寄存器零接触，
CPU 逐字读回比对。

**结果**：`chipdesign_dma_tb` → **PASS，725 周期**（提交 `c73720e`）。

> 共性教训：DMA 的目标窗口不得与其自身控制寄存器重叠；设备 MMIO 写入有副作用，
> 不能当作通用暂存区。

**工具链陷阱（同批修复）**：用 PowerShell 的 `>` 重定向写 hex 会得到 UTF-16LE（含 BOM）
文件，`$readmemh` 无法解析，SRAM 静默保持全 `x`，程序表现为"完全没运行"。
`gen_dma_hex.py` 现自行以 ASCII 无 BOM 写文件。

---

## 七、待确认事项

| # | 事项 | 状态 / 结论 |
| --- | --- | --- |
| 1 | `requant_unit.sv` 两版如何取舍或合流？ | 已结：上游版为当前唯一实现，接口与 TinyCNN-8 一致，且是原接口的参数化演进（+bias/multiplier/activation clamp，SHIFT_WIDTH 取代 SCALE_WIDTH） |
| 2 | `vector_unit` 失去 MMIO 通路是否有意为之？ | **有意**。`ARCHITECTURE.md` 5.1 节说明：只接完整 NPU 顶层，避免 CPU 做数千次底层 MMIO 操作 |
| 3 | `chipdesign_npu_irq_tb`、`chipdesign_requant_tb` 是永久移除还是待改？ | **已删除**，连同其镜像与生成器（6 文件 / 638 行）。算子级验证改由 Icarus 单元回归承担 |
| 4 | `chipdesign_dma_tb` 超时如何修？ | **已修复**（提交 `c73720e`）：SRAM→SRAM 自校验，PASS 725 周期 |
| 5 | `npu_mmio_wrapper.sv`（旧）保留还是删除？ | **已删除**。从未进入生产 filelist，仅 584 行教学/调试代码；`ARCHITECTURE.md` 与 `hardware/soc/README.md` 已同步 |
| 6 | 最终以哪套 NPU 为报告主线？ | **已定：双层口径**（通用计算底座 + TinyCNN-8 应用层），见 `PROGRESS_REPORT.md` 第一、二节 |
| 7 | 进展报告需按新架构改写 | **已完成**，见 `PROGRESS_REPORT.md` |

---

## 八、我方贡献的可核验边界（供报告口径参考）

若报告需要如实标注贡献范围，以下是基于 git 历史的客观事实：

**Yimisda（5 笔提交）**
- 集成 Lab3 SoC 基线（CPU / AXI / 存储 / 调试）+ 接入自研 NPU
- 阶段1：NPU→CPU 完成中断
- 阶段2：`requant_unit.sv`（已被上游替换）
- 阶段3：AXI DMA（`npu_dma.sv`，**未被上游改动**）
- `docs/ARCHITECTURE.md` 初版、Matrix/Vector 单元文档与 Icarus 回归

**mugamucyuu（3 笔提交）**
- TinyCNN-8 加速器 8 个 RTL 模块 + 全部单元级 testbench
- SoC 集成：新 MMIO 包装器、`my_npu_subsystem`、filelist、软件测试程序
- `requant_unit.sv` 替换版、`TINYCNN8_ARCHITECTURE.md`

**Zhiyuan Zhao（3 笔提交）**
- 空间 tile 流水线优化与 MMIO profiling 通道
- 可复现性回归脚本（Python/Swift 工具链）与接受度清单
- `docs/kws_tinycnn8_tiled/` 全套报告与图表、CI workflow

**未被任何人改动的共同资产**
- `vector_unit.sv`、`matrix_unit.sv`、`ws_pe.sv`、`ws_systolic_array.sv` 及其 Icarus 回归
