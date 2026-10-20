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
