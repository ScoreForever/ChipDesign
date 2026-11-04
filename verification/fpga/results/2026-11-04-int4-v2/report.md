# INT4 v2 FPGA 完整验证报告（自动阶段）

> **后续补测已完成：** 7 个发布模型的全部 112 帧已在 FPGA 冷/热重复 224 次通过，正常与生产 RTL 各 137 项通过，24 项实板性能计数器与 RTL 全部相同。见 [模型补充报告](zoo_supplement/report.md)。本次复用完全相同的 50 MHz RTL 和镜像，原时序裕量限制仍保留。

日期：2026-11-04（Asia/Singapore）。对象：远程 `origin/feature/int4-v2`，基线提交 `a600927d128e48d79f51a2b97bd3ac7e4c114d23`。
交付分支：`codex/fpga-int4-v2-validation-20261104`。本分支包含两处 RTL 修复、CDC 构建门禁、补充测试以及原始证据。

## 结论与验收边界

**本轮自动验证通过，当前修复版本已在 EGo1 实板完成 50 MHz 推理、突发传输和忙时恢复测试。完整物理验收尚未完成。**

- 最终布局布线 setup WNS **0.000 ns**、TNS **0.000 ns**，hold WHS **+0.035 ns**、THS **0.000 ns**；setup、hold、脉宽失败端点均为 0。Vivado 判定全部用户时序约束满足。setup 已到报告显示精度的边界，不能称为具有充足裕量，不能据此承诺更高频率。
- 发现并修复新版本 burst 错误返回路径上的 **1 条 CDC-1 Critical**；最终报告 **Critical=0**。保留 109 条 CDC-15 结构警告，逐个核对为 72 bit 请求、37 bit 响应的握手邮箱，物理约束与动态测试通过，没有创建 waiver。
- 四组 RTL 回归分别 **132/132 通过**：普通、FPGA 存储、Tile=8、生产 trace 关闭配置。原始版本与最终修复版本的 132 项周期数、写次数、24 项性能计数器和 operator trace 均相同。
- 实板 **63 种模型/融合配置、126 帧输出**逐字节通过参考比较，另 1 种配置按预期被主机容量检查拒绝；**27 项错误测试**、8 KiB burst 往返及越界恢复、训练权重转换后的 2 帧推理均通过。
- 实板已确认 busy 后停 TCK 1 秒、重复 START、协议 RESET、watchdog 超时清错与新推理恢复全部通过。
- 用户要求先完成自动测试：本 v2 镜像的 RST/S6 按键、断电恢复尚未执行；没有本轮电气波形证据。7 个 zoo 模型已由后续远程更新补齐，并完成全部发布样本的 RTL 与实板验证，详见补充报告；完整数据集精度仍未重评。

机器可读结果见 [validation_matrix.json](validation_matrix.json)。该文件汇总独立测试脚本的最终状态，保留原始记录中的未完成项。

## 设计和工具绑定

| 项目 | 本轮配置 |
|---|---|
| FPGA | EGo1，`xc7a35tcsg324-1` |
| 工具 | Vivado 2025.2；Icarus Verilog；Python `ai_env`；WSL OpenOCD |
| 主时钟 | 板载 100 MHz，经 Clocking Wizard 输出 **50 MHz / 20 ns** |
| 外部 TAP | FT232H，TCK **1 MHz / 1000 ns**，TDO 上升沿采样 |
| NPU | no CPU，4×8 MAC；模型 SRAM 8 KiB、激活 SRAM 8 KiB |
| ABI / numeric format | **4.0 / `0x0f200404`** |
| 数据 | signed i4、unsigned u4、raw i32；低 nibble 在前；Q15 接口乘数压缩为 8 个有效位 |
| 参数 | 每通道 4 B：bias 18 bit、mult 8 bit、shift 6 bit |
| 逻辑算子 | Conv（含 DW/矩形核）、FC、MaxPool、AvgPool/GAP、Add、ReLU、Requant |
| 传输 | IR12：72 bit 普通命令；IR13：32 bit burst write；IR14：流水 burst read |
| 生产 trace | 显式 `CHIPDESIGN_TRACE=0`；实板读回 depth=0、max_ops=0；24 项全局性能计数器保留 |
| 配置下载 | `Xilinx/1234-tulA`，`xc7a35t_0`；DONE=1，CRC_ERROR=0 |

最终 bitstream SHA-256：

```text
8bdb83a721a06f6ce53aa84ce455f6c0da3353af582f7c56c4a18a248fdf2c2c
```

最终实现证据：[accepted_50mhz](evidence/accepted_50mhz/summary.md)、[源文件 SHA-256](evidence/accepted_50mhz/provenance.json)、[构建输入快照](evidence/accepted_source/)、[下载日志](evidence/program/program_console.log)。
板测前逐个校验 provenance 源文件与当前 RTL 一致；下载日志绑定了该 bitstream 路径，随后检查 ABI、容量及输出。没有执行 FPGA 全配置 readback，因此身份依据是下载路径/文件摘要、DONE/CRC 和运行时能力读回，不是配置回读等价证明。
`.bit` 和 `.dcp` 留在本地 `build/int4_v2_50mhz_20261104_r2/`，Git 提交包含报告、摘要、完整文本证据和构建输入。全程没有进行 25 MHz 实现。

## 发现、修复和版本隔离

| 实现 | RTL 状态 | setup WNS / TNS (ns) | hold WHS (ns) | CDC Critical | 判定 |
|---|---|---:|---:|---:|---|
| baseline | 远程原始 v2 | −0.206 / −0.404 | +0.053 | 不作为最终验收依据 | 50 MHz 时序失败，未烧录 |
| r1 | terminal-position 前瞻寄存 | +0.419 / 0 | +0.015 | **1** | 时序通过，独立 CDC 拒绝，未烧录 |
| r2 | 前瞻寄存 + burst 错误接收域处理 + 门禁 | **0.000 / 0** | **+0.035** | **0** | 自动阶段通过，已烧录并实测 |

不能把 r1 的 +0.419 ns 裕量套用到最终 r2；少量 RTL 改动也会改变布局布线。原始失败报告和中间 Critical 报告均随本报告保留：[baseline](evidence/baseline_50mhz/summary.md)、[r1](evidence/timing_fix_50mhz/summary.md)。

### 修复一：缩短位置终点判断的组合依赖

原始最差路径从 `u_soc/i_control/i_npu/issue_p_reg[2]/C` 到 `i_activation/held_data_reg[30]/D`，27 级逻辑，数据路径 19.859 ns（逻辑 5.547 ns、布线 14.312 ns），skew −0.294 ns、uncertainty 0.084 ns；7 个 setup 端点失败。

`npu_program_core.sv` 原先组合计算 `issue_p != tile_positions - 1`，该比较又进入 gather mask/address 和 activation forwarding。现在用 `has_next_q` 保存等价谓词：begin_tile 和 kernel_prepare 初始化，在每次 issue_fire 更新，组尾按 tile_positions 重新建立。没有增加执行阶段或时钟周期。

验证没有仅检查最终输出：对全部 132 项逐个比较修复前后 cycles、writes、全部 24 项 performance 和 operator_trace，差异为 0；独立保存原始结果及基线源码在 [baseline_rtl](evidence/baseline_rtl/)。Tile=8 另行覆盖，生产 trace 关闭配置也重新执行全部 tensor write 比对。

### 修复二：消除 burst 错误对 TCK sticky flag 的直接异步依赖

r1 的真实路由报告暴露 `rsp_mailbox_error_reg[2]/C → overrun_reg/D` 的 CDC-1。原实现将系统域邮箱 error 的非零判断直接用于 TCK 域 sticky overrun，绕过接收域 payload 寄存器。

现在在 ack 同步到达时仍按原握手捕获 `reply_error`，并置接收域 `burst_error_pending`；下一 TCK 用已经捕获的 reply_error 更新 sticky flag。IR12 捕获状态同时纳入该 pending 和已寄存 reply_error，保证最早可见的完成捕获仍返回错误；TMS reset 清除 pending，global reset 清空状态。

新增仿真专门在**最早完成 Capture-DR**读取错误，并在下一次捕获检查 sticky error；另以实板非法 burst 尾地址读确认返回 **`a20002200000000000`**（状态 `0xa2`、地址 `0x22000`），RESET 后新推理正确。不是只把警告隐藏或修改例外。

### 修复三：构建必须拒绝 Critical CDC

两个实现入口在路由 `report_cdc` 后调用 `reject_critical_npu_jtag_cdc`。门禁要求报告格式可识别并拒绝正数 Critical；缺失或畸形报告也不能通过。3 项 Tcl 实际调用测试通过，其中坏例使用本轮 r1 的真实 CDC 报告，保存为 `verification/fpga/fixtures/cdc_critical_burst_v2.rpt`。

该构建门禁负责 Critical；完整 endpoint、额外 Warning 和物理约束检查由 `run_npu_jtag_cdc.py` 执行，两层检查都通过后才上板。

## 最终细致时序分析

### Setup

最终最差路径见 [route_setup_paths.rpt](evidence/accepted_50mhz/route_setup_paths.rpt)：

| 项目 | 最终值 |
|---|---|
| 起点 | `u_soc/i_control/i_npu/issue_p_reg[0]/C` |
| 终点 | `u_soc/i_control/i_activation/q_mask_reg[6][3]/D` |
| 时钟/角 | 系统 50 MHz，同域 rise→rise，Slow Process Corner |
| Requirement | 20.000 ns |
| 数据路径 | 19.817 ns |
| 逻辑 / 布线 | 4.690 ns / **15.127 ns（76.334%）** |
| 逻辑级数 | 23：CARRY4×4、LUT3×2、LUT4×4、LUT5×2、LUT6×11 |
| 时钟 skew / uncertainty | −0.037 ns / 0.084 ns |
| Setup WNS / TNS | **0.000 ns / 0.000 ns** |

剩余路径经过位置/坐标计算、gather mask 和 activation mask 控制。布线占比大，LUT 利用率 83.28%，后续 RTL 或约束变化都必须重新布局布线；当前数字不支持额外频率或大量拥塞余量。STA 的 0.000 ns 是工具输出精度，不代表物理边界精确为零，也不等于已完成温度/电压扫测。

若 RTL 团队继续提升裕量，应优先评估把位置坐标、mask/address 计算在其生命周期内提前寄存或拆级，减少跨区域控制网络；任何增加周期的方案需重新核对 ready/valid、reserve slot、bank 切换、性能计数器与 reference，本轮没有擅自增加这些阶段。

### Hold、脉宽和时钟覆盖

[route_hold_paths.rpt](evidence/accepted_50mhz/route_hold_paths.rpt) 的全局最差 hold：`u_soc/i_host/dr_reg[35]/C → dr_reg[34]/D`，TCK 同域，Fast Process Corner；数据路径 0.392 ns（逻辑 0.186、布线 0.206），skew +0.265 ns，WHS **+0.035 ns**。系统时钟组 hold WHS 为 **+0.053 ns**。不能将全局 hold 路径错误归为系统域路径。

总 setup 检查端点 22,164，失败 0；hold 检查端点 22,055，失败 0；WPWS +3.000 ns、TPWS 0，脉宽失败端点 0。无 no_clock、无 unconstrained internal endpoints、无多时钟寄存器、无组合环。完整时钟对覆盖见 [route_clock_interaction.rpt](evidence/accepted_50mhz/route_clock_interaction.rpt)。跨域时钟对显示 `Ignored / Max Delay Datapath Only`，应结合独立邮箱延迟约束审计理解，不能仅看该标签判为未约束或安全。

### JTAG 外部 I/O 和复位边界

保留原 FT232H 短连线 ≤1 MHz 约束：TDI/TMS 相对 falling TCK min −25 ns、max +32.5 ns；TDO 相对 rising TCK min −20 ns、max +41 ns，包含文档规定的线延迟和边沿 guard。另有半周期等价边沿的 add_delay。输入和输出路径审计全部通过，见 [io_audit](evidence/accepted_50mhz/io_audit/)。本报告不把这些建模假设写成实测波形。

`rst_ni` 是异步外部复位输入，TIMING-18 仍报告其相对 TCK 无输入延迟；同步释放结构和指定 reset entry 例外保留。LED 为无同步接收端的状态输出，其输出路径 false path，内部启动寄存器仍计时。不能写成“所有外部引脚都经过同步 I/O 时序签核”。

## CDC 结构、物理与功能证据

[最终原始 CDC](evidence/accepted_50mhz/route_cdc.rpt) 保留 CDC-3 Info 2、CDC-9 Info 3、CDC-15 Warning 109，无 Critical。109 条警告只接受精确匹配的端点集合，不接受按数量笼统放行。

| 组 | 位宽/路径 | 约束或审计 | 最小 slack |
|---|---:|---|---:|
| request payload | 72 / 72 | datapath-only max 20 ns | +17.838 ns |
| response payload | 37 / 37 | datapath-only max 20 ns | +18.255 ns |
| request toggle 第二级 setup | 1 | 系统域 20 ns | +18.921 ns |
| request toggle 第二级 hold | 1 | 正常 hold | +0.237 ns |
| response toggle 第二级 setup | 1 | TCK 域 1000 ns | +999.241 ns |
| response toggle 第二级 hold | 1 | 正常 hold | +0.160 ns |

详细审计 [route_npu_jtag_cdc.tsv](evidence/accepted_50mhz/route_npu_jtag_cdc.tsv)：仅 toggle 同步器入口 D、reset synchronizer 异步 CLR 按指定端点 cut；第二级继续计时并验证 ASYNC_REG。没有 blanket `set_clock_groups -asynchronous` 覆盖 payload max-delay。

握手安全依据：发送端在请求 pending 期间保持 payload，接收端经同步 toggle 判断新请求后捕获；响应邮箱在下一事务前保持稳定。20 ns payload 物理上界配合两级控制同步及稳定保持使用；不能把 109 bit 分别加同步器后声称数据一致。数字仿真不模拟模拟亚稳态，不给出 MTBF 数值。

动态测试通过 [cdc_review/review.json](evidence/cdc_review/review.json)：原 IR12 测试 100 组相位/频率组合；新 burst 测试 32 组 TCK 半周期 3…130 ns 的相位/速率扫描，包含写入、流水读、地址进位/回绕、pending 过载拒绝、backend backpressure、错误 sticky、Shift-DR 中停钟、回复期间停钟、TMS reset、global reset、丢弃旧请求并重启。高于实板 1 MHz 的数字扫描仅是协议压力覆盖，不作为外部接线可用频率认证。

## 功能回归矩阵

| 层级 | 结果 | 证据 |
|---|---|---|
| Host/compiler/unit | 21/21 PASS | [host log](evidence/test_consoles/int4_v2_host_unit_20261104.log) |
| Board runner/evidence unit | 8/8 PASS | [unit log](evidence/test_consoles/int4_v2_board_unit_20261104.log) |
| Critical CDC gate | 3/3 PASS | [gate log](evidence/test_consoles/int4_v2_cdc_gate_unit_20261104.log) |
| 普通 RTL | 132/132 PASS | [summary](evidence/rtl/summary.json) |
| FPGA SRAM RTL | 132/132 PASS | [summary](evidence/rtl_fpga_mem/summary.json) |
| Tile=8 RTL | 132/132 PASS | [summary](evidence/rtl_tile8/summary.json) |
| 生产 trace-off RTL | 132/132 PASS | [summary](evidence/rtl_production/summary.json) |
| 动态 CDC + 路由端点审计 | PASS | [review](evidence/cdc_review/review.json) |
| 实板基础套件 | 63 配置 /126 帧 PASS；1 项预期主机拒绝 | [report](evidence/board_suite/report.json) |
| 实板基础/补充错误 | 14+13=27 PASS | [基础](evidence/board_suite/report.json)、[补充](evidence/board_extended/report.json) |
| 实板 8 KiB burst/越界/恢复 | PASS | [extended](evidence/board_extended/report.json) |
| 实板 trained PTQ | 2 帧 PASS | [trained](evidence/board_trained/report.json) |
| 实板 busy/停 TCK/watchdog | 5 项检查 PASS | [CDC board](evidence/board_cdc/report.json) |

132 个程序测试覆盖 4/8 列、fused/plain、JTAG 命令路径、训练权重及新 v4 算子；每个已编译程序检查完整输出和 tensor write 事件。RTL 套件另包含 20,000 个独立有理数参考的单次舍入 requant 向量（II=1）、signed/u4 MAC 穷举、SRAM、trace、随机读写 stall 与 drain/performance scoreboard、16 项非法 descriptor 和生命周期测试。

生产测试明确使用 `SYNTHESIS`、`CHIPDESIGN_FPGA_MEM`、`NPU_ENABLE_WAVE_TRACE=0`、`NPU_ENABLE_OP_TRACE=0`。trace 地址按预期拒绝，性能计数器与 tensor write 结果继续检查；不能将仿真 trace-enabled 通过直接代替生产配置验证。

板测 fixture 覆盖 32 个基础模型的 fused/plain 组合：包括 i32、i4/u4、DW、矩形核/1D、通用池化/GAP、residual Add、原位 src2、行 padding、全 8 KiB 激活、边缘和最大 K、slot pressure。`tinycnn_plain` 预期超过激活容量，主机拒绝，未算作硬件推理通过；其余 63 配置各执行原输入和逆序输入两帧。参考主要来自项目软件解释器，并非所有算子都有独立实现，不能声称形式等价或覆盖所有数值组合。

补充错误覆盖 old ABI、错 cols、reserved shift、DW channels、非法 pool window、Add src2 overlap、Add fused pool、u4+i32 flags、FC 误设 DW、非法 unfused pool1d，以及未对齐/未映射地址和未知 opcode。8 KiB 激活 2048 word 往返覆盖 64-word RPC 分块；最后合法 word 后的非法 word 被拒绝，错误地址/状态均检查，随后新推理通过。

现有训练 NPZ 摘要 `1732c470ec65f2d2c0ebc96dd93326dabb058277b71eef1bb3717d2ddee2f83a`。它经现有转换器执行有损 `/16` INT8→INT4 PTQ，两帧各 **3230 cycles、404 writes、2440 MAC issues**，与相应 RTL 相同，实板时间按 50 MHz 为 64.6 μs（计数器核心执行时间，不含 JTAG 上传/下载）。此项验证转换后硬件结果，未验证原 INT8 数值等价或数据集精度。

## 实板停钟与恢复观察

使用 16 层 Conv/Requant、64×64 输入的加长程序；每次动作前在连续 OpenOCD RPC 中读取 busy，记录值均为 **3**（busy+model_ready）。

1. 停止主机 JTAG 请求、TCK 无扫描 1 秒，恢复后状态 **10**（done+model_ready），完整输出与参考一致。
2. confirmed busy 时重复 START，返回预期 error 4。
3. confirmed busy 时协议 RESET，busy/model_ready/input_ready/done/fault/error、周期/issue/write 全部清零。
4. watchdog 设为 1000 cycles，产生 error 10；RESET 清错。
5. 上述取消/超时后重新上传并完成参考比较，新推理通过。

这些是协议复位，不是物理按钮复位。基础 board_suite 中 `manual_pending` 是其通用待测列表，原始文件未改写；四项自动补充结果以 board_cdc 为准，最终合并清单见 validation_matrix。基础 evidence checker 返回 2，表示自动证据核对通过但物理项目未完成，不能误记为全部失败，也不能改写成完整物理 PASS。

测试完成后执行协议 RESET，确认空闲且 error=0，关闭 OpenOCD 并解除 FT232H USB 转发。开发板保留本轮 v2 的易失配置，没有写配置 Flash。

## 资源和仍需关注的问题

| 资源 | 使用 / 可用 | 利用率 |
|---|---:|---:|
| LUT | 17,322 / 20,800 | 83.28% |
| FF | 6,249 / 41,600 | 15.02% |
| BRAM36 tile | 4 / 50 | 8.00% |
| DSP | 20 / 90 | 22.22% |
| MMCM | 1 / 5 | 20.00% |

配置/BRAM DRC 指定检查通过。原始 DRC 仍有 DSP 流水化建议：DPIP-1 42、DPOP-1 17、DPOP-2 14、DPREG-4 9；methodology 仍有 SYNTH-6 4（RAM 未合并 output register）、LUTAR-1 1（`rst_ni & locked` 组合驱动异步 reset）和 TIMING-18 1（rst_ni）。本报告没有将它们写成“零警告”。

RTL 对接优先级：继续改善临界 setup 裕量；复核 reset 合成网络的毛刺/复位脉宽假设并补齐实板测量；任何 BRAM/DSP 流水级调整都要重新执行 tensor、stall、drain、CDC 和 50 MHz 路由验收。当前有界 mailbox CDC 109 条属于已审查结构警告，真正新增的 CDC-1 已修复。

## 未执行及后续验收

| 项目 | 状态与原因 |
|---|---|
| 本 v2 镜像 RST/S6 闲时/忙时按键 | NOT RUN，用户要求现场操作稍后；旧 INT4/no_cpu 结果不沿用 |
| 本 v2 镜像完全断电、重新上电和重下载 | NOT RUN，同上 |
| TCK/TDI/TDO/复位电气波形、毛刺、复位脉宽 | NOT RUN，无本轮仪器证据 |
| 配置 Flash 与自动启动 | NOT RUN，当前仅易失配置下载 |
| 完整数据集识别精度 | NOT RUN，112 帧发布样本的模型级补测不替代全测试集重评 |
| 温度/电压、多板、长期可靠性/MTBF | 无覆盖，不作保证 |

后续现场测试必须以本报告 bitstream/source 为对象，重新打开监测窗口并确认 busy，再提示按键；断电前关闭下载/USB 转发并断开全部供电，恢复后重下载并重跑参考推理。用户尚未要求进入操作窗口，本轮未发送按键/断电提示。

## 复现与证据使用

在本分支仓库根目录，使用已有 Python、iverilog/vvp 和 Vivado 环境。每次 output 指定新目录，实板脚本必须串行占用唯一 TAP：

```powershell
python hardware/npu/scripts/run_program_regression.py --output build/recheck_rtl --trained-model software/models/tinycnn8_flatten_strong.npz --jobs 4
python hardware/npu/scripts/run_program_regression.py --output build/recheck_mem --trained-model software/models/tinycnn8_flatten_strong.npz --define CHIPDESIGN_FPGA_MEM --jobs 4
python hardware/npu/scripts/run_program_regression.py --output build/recheck_tile8 --trained-model software/models/tinycnn8_flatten_strong.npz --define NPU_TILE=8 --jobs 4
python verification/fpga/run_int4_v2_production_rtl.py --source-evidence build/recheck_rtl --output build/recheck_production --jobs 4
$env:CHIPDESIGN_TRACE='0'
python hardware/fpga/scripts/build_nocpu.py --vivado-root D:/AMDDesignTools/2025.2/Vivado --output build/recheck_50mhz --jobs 4 --mhz 50 --cols 8 --implement
python verification/fpga/run_npu_jtag_cdc.py --static-evidence build/recheck_50mhz --output build/recheck_cdc
```

先确认时序、CDC 和 provenance 通过，再按现有 `scripts/lab6/program_board.tcl` 下载，启动 FT232H/OpenOCD。以实际新 bitstream 摘要传入 board suite；随后依次运行 extended、trained、board_cdc，不能并行操作硬件：

```powershell
python verification/fpga/nocpu_board_suite.py --bitstream build/recheck_50mhz/fpga_top.bit --sha256 <实际SHA256> --provenance build/recheck_50mhz/provenance.json --output build/recheck_board --repeat 2 --timeout 30
python verification/fpga/int4_v2_board_extended.py --output build/recheck_extended
python verification/fpga/int4_v2_board_trained.py --rtl-evidence build/recheck_rtl --output build/recheck_trained
python verification/fpga/nocpu_board_cdc.py --output build/recheck_board_cdc
python verification/fpga/nocpu_evidence_check.py build/recheck_board
```

`evidence/` 保存输入、预期/实际输出、命令、package、编译/仿真日志、各路由报告和源快照。较大的 `.log` / `.jsonl` 以 `.gz` 无损存储；需要读取时使用 gzip 解压。单元日志中的故意 SHA mismatch 属于拒绝错 bitstream 的负例，8 项测试最终为 OK。

本目录 `.gitattributes` 禁止换行转换，`evidence_sha256.json` 列出每份归档文件的 SHA-256（排除 manifest 自身）。提交后额外核对 Git blob 与清单一致，防止忽略规则或 CRLF 改写证据。
