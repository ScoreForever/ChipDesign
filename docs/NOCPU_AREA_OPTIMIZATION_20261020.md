# 无 CPU SoC 面积优化及 EDA 交接

日期：2026-10-20。修改直接位于 `exp/no_cpu` 工作区，基于 `48958c0`。
本轮只修改 RTL、仿真和工具入口配置，没有运行 Genus/DC/Vivado 综合或任何后端 EDA。
实际面积、最高频率和 2×2 mm² 是否闭合，由下一轮 EDA 评估。

## 默认交付配置

`my_soc_top` 默认参数：`WEIGHT_SRAM=1, SPATIAL_TILE=16, ARRAY_ACC_WIDTH=18, PSUM_WIDTH=23`。
4×8 计算阵列、INT8 权重/激活、Q31 量化、INT32 bias/logits 和 1–8 类模型能力不变。
JTAG ABI 仍为 1.0，Python 主机端不需要修改。

```text
JTAG -> 命令控制器 -> NPU
                      ├─ 同步权重预取/装载 -> npu_weight_store -> 1 × 8 KiB SRAM
                      ├─ 1 KiB 激活：保留原来的 byte bank 结构
                      ├─ 4×8 PE：内部 18-bit 局部和
                      ├─ tile16：23-bit 无偏部分和 -> INT32 加 bias -> INT32 MaxPool
                      └─ 原 Q31 requant / INT32 logits
```

## 实际实现的改动

1. 新增 `hardware/npu/rtl/npu_weight_store.sv`，使用一个同步 32-bit 物理端口。
   一个 64-bit 逻辑权重行由两次 32-bit 访问组装；写入同样分为两拍。
   `NPU_USE_RA1SHD_WEIGHT` 选择真实 `RA1SHD_2048x32M8`；没有该定义时采用
   同端口时序的行为存储，供公开仿真/FPGA 使用。行为模型不代表 ASIC 宏推断。
2. 装载器分离“发起 SRAM 请求”和“写 PE 权重 bank”。允许提前读取下一组首行，
   返回被阻塞时保持数据和端口所有权，直到旧任务全部退出该 PE bank 才写入。
   因此不会为了预取而提前覆盖仍在使用的权重。
3. 权重计算/空闲查询共用物理读口，FF 对照配置也做了读口合并，不增加镜像存储。
   控制器接入 `weight_ready`、`debug_valid`，同步查询可等待；后端不返回时有 watchdog。
   主机的请求/响应和错误码保持原契约，结果背压期间不重执行命令。
4. 阵列局部和从 32 收至 18 bit，出口符号扩展至 INT32。四个 INT8 乘积的范围
   为 `[-65024,65536]`，不会损失数值精度。
5. `psum_buf` 从 32 收至 23 bit，存储未加 bias 的乘积和。当前最大归约 K=160，
   范围 `[-2600960,2621440]`，23-bit signed 足够。最后在 **INT32 MaxPool 之前**
   按 INT32 wrap 加 bias，保持任意 INT32 bias 的语义。不能把 bias 移至池化之后。
   `PSUM_WIDTH=32` 对照配置保留原 bias-first 单加法器路径。
6. 将原先未生效的 `SPATIAL_TILE` 真正参数化为 16/8/4，同步调整 tile 计数、tag、
   窗口数量、地址及轮转。小 tile 虽减少缓冲，但仿真实测较慢，所以默认仍用 16。

复位清除请求/响应有效性和状态，不清 SRAM 内容；软复位继续取消 model/input valid，
要求主机重新完成模型提交。模型跨推理复用、更新、空闲查询、超时与中途复位均有回归。
独立融合核/旧 MMIO 默认保留 `WEIGHT_SRAM=0`，避免给原来无 ready 的装载者引入新节拍。
直接启用独立核同步权重模式时，装载者必须遵守新的 `host_weight_ready`。

## 仿真结果与配置选择

每个配置检查 12 个模型/输入用例，每例连续推理两次：1–8 类随机、impulse、extreme、
INT32_MIN/MAX bias 和 full-range INT8 溢出边界。对比每个 Pool1/Pool2 byte、全部 logits，
并由独立事件记分板检查每拍装载数据/地址、bank 所有权、激活 gather、阵列返回、
部分和、池化/量化输入、写回、完成和计数器。

| 配置 | C1 | C2 | FC | 总周期 | 相对原周期 |
|---|---:|---:|---:|---:|---:|
| FF / tile16 / 32-bit 对照 | 1000 | 1480 | 325 | 2805 | — |
| FF / tile16 / 18-bit PE、23-bit psum | 1000 | 1480 | 325 | 2805 | 0% |
| SRAM / tile16 / 32-bit 对照 | 1063 | 1573 | 392 | 3028 | +7.95% |
| **SRAM / tile16 / 18-bit PE、23-bit psum（默认）** | **1063** | **1573** | **392** | **3028** | **+7.95%** |
| SRAM / tile8 / 窄位宽 | 1588 | 2368 | 392 | 4348 | +55.01% |
| SRAM / tile4 / 窄位宽 | 2664 | 3984 | 392 | 7040 | +150.98% |

若实际仍满足 50 MHz，默认核心计算为 60.56 µs，原版为 56.10 µs；
这是由周期换算的时间，不是新版本的 STA/板测结果，也不含主机传输。
阵列发射/返回仍为 2440 次、权重行装载 760 次，两级池化写回 800 B，未减少计算工作。
工具入口对默认配置设置不超过原版 10% 核心周期的回归预算。

另外验证：

- 同步存储独立测试覆盖 8/16/32/64-bit 逻辑行，每种 514 次数据检查，含连续读取、
  模型替换、返回背压及在途读复位后重新运行。
- 使用课程提供的 **真实 SRAM Verilog** 再跑上述存储测试和默认整网配置，结果及周期一致。
- 行为存储和真实 SRAM 模型分别通过无 CPU 全回归：三组 1/4/8 类控制器测试，
  每组 5923 条命令、3223 次查询检查；741 条 JTAG 命令、171647 个 TCK 边沿；
  639 条真实 Python 主机 API 命令回放。
- 旧融合核、MMIO、量化器和容量测试另行回归。机器可读验收证据见
  `docs/validation/nocpu_area_20261020.json`。

真实宏模型仅放在被 Git 忽略的 `build/private_ip/`；不提交工艺 IP。Icarus 的宏模型仿真
证明接口功能，不能代替宏访问时间、setup/hold 和物理视图一致性的 EDA 检查。

## 面积依据和目标预算

| 主数据存储 | 原 RTL 位数 | 默认新 RTL 的标准单元/可推断阵列位数 |
|---|---:|---:|
| 权重 | 16384 | 0，移至硬宏；另有少量读写暂存寄存器 |
| 激活 | 8192 | 8192 |
| 部分和 | 4096 | 2944 |
| 池化窗口 | 1024 | 1024 |
| 合计 | 29696 | 12160 |

以上主阵列声明量减少 17536 bit（59.05%），不等于全芯片 FF 面积下降 59%。
阵列内部及 deskew 声明最多另少 840 bit；综合器可能早已消除部分符号扩展，不能重复计收益。
没有删除模型更新、参数 staging、结果快照、CDC 或诊断能力来换面积。

代价是新增 **一颗固定 8 KiB 宏，约 0.401444 mm²**，只使用其中 2 KiB 权重空间；
不能将未使用容量裁小，也不能把“FF 减少量”直接当成净面积收益。
目标是把剩余标准单元压到约 1.4–1.5 mm²，加宏后约 1.8–1.9 mm²，作为 2 mm die
的候选预算。这是设计目标，**本次没有测得新面积，不能保证已经达标**。
若小容量宏/compiler 后续可用，可只替换此存储 wrapper 的物理实现而保留上层握手。

本轮没有采用权重/激活共享单端口宏：C2 的激活读取、权重预取和输出写回会竞争带宽，
很可能进一步增加延迟；现有方案用一颗宏取得主要存储收益，保留激活端口并行度。

## 交给 EDA 同学的入口

源文件列表：`hardware/soc/sim/filelists/chipdesign_nocpu.f`，现有 **8 个源文件**。
顶层 `my_soc_top`，不恢复 CPU/AXI/共享主存/DMA。

Genus 映射入口仍是 `hardware/soc/synth/scripts/synth_genus.tcl`，现在默认：

- 将 SRAM TT Liberty 加入目标库；可用 `EDA_WEIGHT_SRAM_LIB` 指定。
- 传入 `NPU_USE_RA1SHD_WEIGHT`，实例化真实硬宏。缺失库则明确失败。
- 保留原有 `EDA_TARGET_LIBS`、`EDA_CLOCK_NS`、`EDA_IO_DELAY_NS`、
  `EDA_CLOCK_UNCERTAINTY_NS` 等系统约束设置。

```sh
# 在仓库根目录、原有 EDA 环境和标准单元库设置上补充：
export EDA_WEIGHT_SRAM_LIB=/lec/lab4/soc_cv32e40p_template_pnr-stu/src/macro/RA1SHD_2048x32M8/RA1SHD_2048x32M8_tt_1p8_25C_syn.lib
export EDA_NPU_WEIGHT_SRAM=1
export EDA_NPU_TILE=16
export EDA_NPU_ARRAY_ACC=18
export EDA_NPU_PSUM=23
# 使用新的 EDA_RESULT_DIR 后，再由 EDA 同学运行原综合入口。
```

`EDA_NPU_WEIGHT_SRAM=0` 选择 FF 合并读口对照；`EDA_NPU_ARRAY_ACC=32`、
`EDA_NPU_PSUM=32` 可分别回到宽位宽。`EDA_NPU_TILE=8/4` 是已验证的面积/延迟折中，
不推荐作为默认。共享选择逻辑位于 `nocpu_area_config.tcl`。
DC 备用入口需另提供匹配的 `EDA_WEIGHT_SRAM_DB`；仅有 Liberty 不能直接作为 DC 的 .db。
本轮只用普通 Tcl 和模拟工具命令检查了入口的配置/失败分支，没有实际运行 Genus/DC。

关键物理注意事项：

1. 映射后检查恰好 **1 个 RA1SHD_2048x32M8**，且无未解析模块。
   若只使用行为 RAM 或只定义 SYNTHESIS，可能仍展开为 FF，不能算 SRAM 方案。
2. 预期实例路径：`i_control/i_npu/g_weight_sram/storage/i_sram`。
   旧的 `i_mainmem/i_mainmem` 放置脚本不适用，需移动/供电到新实例。
3. 宏视图目录为 `/lec/lab4/soc_cv32e40p_template_pnr-stu/src/macro/RA1SHD_2048x32M8/`。
   宏 LEF SIZE 为 554.595×723.85 µm；核对其 METAL7/8 obstruction 与 6-metal 工艺的关系，
   使用匹配的 Liberty/LEF/GDS/CDL，不盲删 obstruction。
4. 在约 1700 µm core 中扣除宏及 halo 后，重新计算可放置行面积和密度；分别报告
   标准单元、宏、时钟/修复及最终 die 面积，避免只比较标准单元降幅。
5. 检查 SRAM 到 PE 的读返回路径，以及窄 psum 加回 bias 后到 INT32 MaxPool 的路径。
   后者有额外加法，23-bit FF 节省是否抵消逻辑及修复开销，需要与 PSUM_WIDTH=32 比较。
6. 以默认 tile16 为主做约 2 mm die 的布局布线、STA、DRC/antenna 和 JTAG SDF 后仿。
   严格 2000 µm 限制需考虑网格取整，前次 1700 µm core 实际得到 2000.32 µm die。

## 复现实验

```sh
python hardware/npu/scripts/run_area_experiments.py --output build/area_experiments
python hardware/soc/sim/scripts/run_nocpu.py --output build/nocpu_area

# 实际课程模型从有访问权限的公共模板取得，保持在 Git 忽略目录中：
python hardware/npu/scripts/run_area_experiments.py --profiles sram16 --macro-model build/private_ip/RA1SHD_2048x32M8.v --output build/area_macro
python hardware/soc/sim/scripts/run_nocpu.py --macro-model build/private_ip/RA1SHD_2048x32M8.v --output build/nocpu_macro
```

CI 增加公开行为模型的同步存储与 tile 配置回归，不上传课程宏模型。

---

## 2026-10-20 EDA 实测结果（新增）

本节给出上述默认配置的实际 Genus、Innovus 与 Xcelium 结果，替代前文“尚未运行 EDA”的状态说明。
实验基于 `exp/no_cpu` 提交 `6957d2ce28798fb8697d3b64691bbc6cde8061bb`。启用真实 SRAM 综合分支时发现
`debug_weight_data` 在声明前被引用，因此实验网表使用该提交加一项最小声明顺序修正；修正副本位于
`backend_experiments/no_cpu_areaopt_20261020/rtl_patch/tinycnn8_npu_fused.sv`。除该声明移动外未修改功能逻辑。

### 结论

- 1700.16 µm × 1700.16 µm core 可以完成布局布线，最终 die 为
  **2000.32 µm × 2000.32 µm = 4.001280 mm²**。相对严格的 2 mm × 2 mm 每边仅多
  0.32 µm，这是放置网格取整；按课程“约 2 mm × 2 mm”口径可视为达到目标。
- Innovus 最终 `verify_drc` 为 **0**，process antenna 为 **0**。
- 50 MHz 下 setup WNS **+2.908 ns**、hold WNS **+0.021 ns**，TNS 均为 **0**。
- SDF 门级后仿通过：`POSTLAYOUT_PASS: no_cpu JTAG inference OK TCK_edges=150205`。
- 相对上一版无 CPU 实现，布局后 `report_area` 从 2.686591 mm² 降至
  **1.940345 mm²（-27.78%）**；NPU 层次面积下降 **32.09%**。

### 实验条件

| 项目 | 值 |
|---|---:|
| Genus | 19.17-s144_1 |
| Innovus | 21.16-s078_1 |
| Xcelium | 21.09-s013 |
| 工艺/标准单元 | TSMC 180 nm / `tcb018gbwp7t` |
| SRAM | 1 × `RA1SHD_2048x32M8`（8 KiB） |
| PVT/RC | TT, 1.80 V, 25 °C / typical captable |
| `core_clk` | 20 ns（50 MHz） |
| `jtag_clk` | 1000 ns（1 MHz） |
| 默认参数 | SRAM=1, tile=16, PE acc=18 bit, psum=23 bit |

### 综合结果

| 指标 | 实测值 |
|---|---:|
| 映射叶实例数 | 45,554 |
| Genus cell area（含 SRAM） | 1,681,627 µm² |
| SRAM memory area | 401,444 µm² |
| 标准单元面积（cell area 扣除 SRAM） | 1,280,184 µm² |
| Genus 估算 net area | 465,634 µm² |
| Genus total area（cell + net） | 2,147,261 µm² |
| 综合 setup WNS | +2.652 ns |
| 未解析引用 / 空模块 | 0 / 0 |
| 硬宏实例数 | 1 |
| 矢量无关功耗估算 | 33.767 mW（仅早期估算，不是活动文件驱动或版图签核功耗） |

相对上一版无 CPU 综合结果（2,355,548 µm²、无硬宏），新版本包含硬宏的 Genus cell area
下降 28.61%；若只比较标准单元，则下降 45.65%。不能将后者当作整芯片面积降幅，因为
0.401 mm² SRAM 宏仍真实占据版图。

### 布局布线几何与利用率

| 指标 | 实测值 |
|---|---:|
| Core | 1700.16 µm × 1700.16 µm |
| Core 面积 | 2.890544 mm² |
| Die | 2000.32 µm × 2000.32 µm |
| Die 面积 | 4.001280 mm² |
| Core-to-die margin | 150.08 µm |
| SRAM 物理框 | (300, 500)–(854.595, 1223.85) µm |
| 最终 density（不含 filler） | 56.319% |
| 最终 density（含 filler） | 100.000% |
| 布线总长度 | 3,067,110 µm |
| Via 数 | 389,033 |

`report_area` 是层次单元物理面积求和，不等于 core 或 die 面积：core 还需要宏 halo、布线通道、
电源网络与空白；die 还包含 150.08 µm 边缘和 I/O 环。

### 布局后层次面积

| 层次 | 实例数 | 面积 (µm²) | 面积 (mm²) | 占 `report_area` |
|---|---:|---:|---:|---:|
| `my_soc_top` | 48,729 | 1,940,345 | 1.940345 | 100.00% |
| `i_control` | 48,064 | 1,679,325 | 1.679325 | 86.55% |
| `i_control/i_npu` | 43,790 | 1,579,997 | 1.579997 | 81.43% |
| `i_control/i_controller` | 4,267 | 99,186 | 0.099186 | 5.11% |
| `i_host` | 579 | 18,999 | 0.018999 | 0.98% |

NPU 内部可保留层次如下。`NPU 顶层其他逻辑` 是 NPU 总面积减去三个独立子层次，包含窗口/
激活/部分和存储、池化、控制、地址与时钟修复逻辑。

| NPU 单元 | 面积 (µm²) | 面积 (mm²) | 占 NPU |
|---|---:|---:|---:|
| `array`（4×8 计算阵列） | 213,657 | 0.213657 | 13.52% |
| `g_weight_sram.storage`（SRAM + 包装逻辑） | 392,923 | 0.392923 | 24.87% |
| `requant` | 66,486 | 0.066486 | 4.21% |
| NPU 顶层其他逻辑 | 906,932 | 0.906932 | 57.40% |
| **NPU 合计** | **1,579,997** | **1.579997** | **100.00%** |

这里 `report_area` 对 SRAM 层次计入 0.392923 mm²，而 Genus/LEF 物理宏框面积为
0.401444 mm²；前者用于同一份 Innovus 层次报告内的占比，宏的实际 floorplan 占位应以后者为准。

上一版 NPU 为 2.326747 mm²；其中 `array` 0.215942 mm²、`requant` 0.066506 mm²，二者几乎
不变。主要收益来自原先吸收在 NPU 顶层的权重 FF/选择网络以及相关宽位存储：扣除阵列、
requant 和新权重 SRAM 层次后的其他逻辑，从 2.044300 mm² 降至 0.906932 mm²（-55.64%）。

### 布局后 STA 与物理检查

| 检查 | 结果 |
|---|---:|
| Setup WNS / TNS | +2.908 ns / 0.000 ns |
| Hold WNS / TNS | +0.021 ns / 0.000 ns |
| Setup / hold violating paths | 0 / 0 |
| Max capacitance / transition / fanout / net length DRV | 0 / 0 / 0 / 0 |
| Innovus geometry DRC | 0 |
| Innovus process antenna | 0 |
| Regular connectivity | 2：`VDDPST/VSSPST` I/O 环未布线（与既有流程相同） |
| Core PG connectivity | 93 dangling-wire markers，集中在 SRAM 电源条带端点 |
| `checkPlace` | 35 个 package bond-pad 物理标记与 I/O pad 重叠 |

后两项不会阻止本次 Innovus DRC/antenna 与门级后仿通过，但意味着这仍是课程级 Innovus closure，
不是 foundry signoff。正式 GDS 前需清理 SRAM PG dangling shapes、决定 bond-pad 标记是否应进入
流片 GDS，并另做 Calibre DRC/LVS/PEX。

### SDF 门级后仿

| 指标 | 结果 |
|---|---:|
| 仿真网表 | `my_soc_top_hier_postpnr.v` |
| SDF | `my_soc_top_tt_io.sdf.gz`，MAXIMUM delay |
| Path delay 标注 | 336,433 / 348,598（96.51%） |
| Timing check 标注 | 88,679 / 91,755（96.65%） |
| SDF annotation errors / warnings | 0 / 25,912 |
| 功能结果 | PASS，4 logits 与 golden 一致 |
| TCK 边沿 | 150,205 |
| 仿真结束时间 | 2,403,331 ns |
| time-zero PAD bus conflict | 5，非致命，随后测试通过 |

大量 SDF warning 主要来自负延迟钳到 0、标准单元 timing-check 形式与连续赋值路径映射；应结合
96% 以上标注率和 0 annotation error 解读，不能写成“100% 无警告后仿”。

### 导出物

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `my_soc_top.def` | 66,998,316 | `464a825d9758e606c05ea29bae48831887a073f22ff8060501769a7736b0a34f` |
| `my_soc_top.gds.gz` | 11,467,803 | `4f517d22ac03ec165faf0e0f5db039f84383956d642ed98d9e22d9fe6bbd39d7` |
| `my_soc_top.lef` | 2,580 | `8be32db239e9185810527bbb977752e1712e7f8a310ddfbcb5a9ebf453680b3e` |
| `my_soc_top_hier_postpnr.v` | 7,118,806 | `bb520d8fd7378fc6e5833abca50c55f65c11625d7d2faacd80c0b4b0b315b2` |
| `my_soc_top_flat_lvs.v` | 14,654,404 | `1cf7403d4e40bdeda2e171f9713389c7b4c067710e6454d01f3e5f2043aa1119` |
| `my_soc_top_tt_io.sdf.gz` | 7,544,477 | `c4ae8a8b939960b74985654d1fe187e8e6fc628b17583e2e0699aa1db715525c` |
| `my_soc_top_tt_io.spef.gz` | 16,800,730 | `e7b3077abf3316cb3b03fc7929637b44c9b013b8077cc559dedddbf7942942df` |
| `my_soc_top.sdc` | 1,681 | `43b9f11b6557ebdb00a6c162d4b63cae437d2b2ee917c640e37d9e4393f9cbea` |

服务器实验根目录：
`/home/s2400012724/chip-fullstack-2026/ChipDesign_postsim_exps/pnr_no_cpu_areaopt_20261020/`。
仓库内原始证据位于 `eda_results/2026-10-20/no_cpu_areaopt/`，复现配置与脚本位于
`backend_experiments/no_cpu_areaopt_20261020/`。
