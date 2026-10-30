# FPGA / CDC 完整验证报告

**验证日期：** 2026-10-30  
**验证对象：** 远程 `origin/main`，提交 `f63a49132ec1b2131d1c59adf86ec9cfc0bd5a3a`  
**目标器件：** EGo1 / Artix-7 `xc7a35tcsg324-1`  
**工具：** Vivado 2025.2、ModelSim SE-64 2019.2、Icarus Verilog / vvp、Python  
**工作目录：** `D:\IWS\Advanced_SoC\ChipDesign\build\main_latest_validate_20261030`

## 1. 验证结论

本轮最新 `main` 的 NPU/JTAG RTL 功能回归和 CDC 复位专项仿真通过。Vivado 完成综合、布局布线、DRC 和 bitstream 生成，但 **50 MHz setup 时序签核失败**。因此本次结论为：

| 验证领域 | 结论 |
|---|---|
| NPU/JTAG 功能仿真 | PASS，70 个场景全部通过 |
| CDC 复位与停钟功能仿真 | PASS |
| CDC mailbox 结构及时序约束审计 | payload 与同步器检查通过；保留 109 条 CDC-15 结构警告 |
| FPGA 综合 / 布局布线 | 完成；BRAM 专项检查通过 |
| 50 MHz routed setup 时序 | **FAIL，WNS = −3.961 ns，TNS = −5382.919 ns** |
| FPGA 实板下载及实板推理 | NOT RUN；本轮 bitstream 未下载到板卡 |

生成 bitstream 只说明 Vivado 完成了 bitgen，不代表满足时序门禁。当前版本不能签核为 50 MHz FPGA 时序通过，也没有本次最新镜像的实板验证结论。

## 2. 功能验证

在提交 `f63a491` 的隔离 checkout 中运行完整 ABI 2 可编程 NPU/JTAG 回归。结果为 **70 个程序测试全部通过**，包括：

- 4×8 与 4×4 两种阵列配置；融合和非融合执行路径。
- Conv、FC、MaxPool、ReLU、Requant，包含 stride、非对称 padding、负数池化和长 K。
- INT32 输入与后处理、量化边界、单 MAC、训练权重模型。
- 双 SRAM 行为、8192-byte 激活区完整覆盖、高地址位访问、结果槽压力及随机读写停顿/排空。
- 非法 opcode、保留字段、stride、shape、destination、source、multiplier、shift 等拒绝检查，以及生命周期、abort、timeout、不完整事务处理。
- JTAG_TEST 的融合与非融合 CNN 场景均通过；系统时钟周期为 10 ns，测试台通过人工产生 TCK 边沿与其交错，执行实际 TAP/transport RTL 命令轨迹。
- 另有 20,000 个量化向量、双 SRAM 语义、trace 和随机读写停顿测试通过。

回归原始摘要：`build/cdc_focus_program_regression_20261030/summary.json`。报告仅声明脚本所覆盖场景通过，不代表穷尽状态空间、代码覆盖率收敛或形式验证。

## 3. CDC 功能验证

### 3.1 复位、TCK 停顿与恢复

通过 `verification/fpga/run_cdc_reset.ps1` 运行 ModelSim 专项 testbench。日志结果为 `PASS CDC reset`，且 `Errors: 0, Warnings: 0`。该场景验证：

1. TCK 域复位撤销后必须经过同步级才释放。
2. 先完成一次 mailbox 传输，确认数据一致。
3. 让目标端保持一个未完成事务并停止 TCK。
4. 在 TCK 停止期间断言异步复位，确认复位能立即生效并清除挂起状态。
5. 保持 TCK 停止期间撤销复位，确认 TCK 域不会提前释放。
6. 恢复 TCK、完成同步释放后，再验证一笔新传输正确。

日志：`build/cdc_reset_latest_main_20261030_host/sim.log`。该专项使用通用 `cdc_2phase` mailbox 和 `rstgen`；真实 `npu_jtag_transport` 的命令交互另由完整回归中的 JTAG_TEST 场景覆盖。

### 3.2 Routed CDC 结构检查

对本轮 routed checkpoint 执行 `report_cdc -details` 与 `report_clock_interaction`：

| CDC 检查项 | 结果 |
|---|---:|
| CDC-3：ASYNC_REG 单比特同步器 | 2 项 Info |
| CDC-9：ASYNC_REG 异步复位同步器 | 3 项 Info |
| CDC-15：clock-enable 控制的 bundled-data CDC | 109 项 Warning |

109 条 CDC-15 对应 72-bit 请求 payload 和 37-bit 响应 mailbox。它们是由稳定保持的多位 mailbox 结构引起的工具告警，未被隐藏或豁免。RTL 结构和握手保持窗口有说明，但告警本身也不等同于 CDC 协议形式证明或亚稳态概率证明。

### 3.3 CDC payload 与同步器时序约束

`route_npu_jtag_cdc.tsv` 来自本次 routed design 的专用审计：

| 信号组 | 约束 / 检查 | 路径数 | 上限 | 最小 slack |
|---|---|---:|---:|---:|
| request payload | bundled max-delay | 72 / 72 | 20 ns | +18.147 ns |
| response payload | bundled max-delay | 37 / 37 | 20 ns | +18.359 ns |
| request toggle 第 1→2 级 | setup / hold 均检查 | 1 / 1 | 20 ns setup | +19.192 / +0.160 ns |
| response toggle 第 1→2 级 | setup / hold 均检查 | 1 / 1 | 1000 ns setup | +999.241 / +0.160 ns |

异步 toggle 输入级按设计只切断到第一级 D 的同步 timing 分析；级间路径仍保持同步检查。四组复位同步器的异步 CLR 端按专用约束处理，报告确认复位同步器属性和目标引脚均存在。

CDC 证据：

- `build/fpga_main_20261030/route_npu_jtag_cdc.tsv`
- `build/fpga_main_20261030/cdc_audit/cdc.rpt`
- `build/fpga_main_20261030/cdc_audit/clock_interaction.rpt`

## 4. FPGA 实现与 50 MHz 时序

本次完整目标为 `fpga_top`，器件 `xc7a35tcsg324-1`。板载 100 MHz 输入经 Clocking Wizard 产生 50 MHz 系统时钟，周期约束为 20.000 ns。Vivado 完成综合、放置、布线和 bitstream 生成；但最终验收门禁因 setup 违例失败。

| 指标 | 本次结果 |
|---|---:|
| Requested system clock | 50 MHz |
| Routed period | 20.000 ns |
| Setup WNS | **−3.961 ns** |
| Setup TNS | **−5382.919 ns** |
| Setup failing endpoints | 2534 |
| Hold WHS | +0.031 ns |
| Hold failing endpoints | 0 |
| LUT | 16,488 / 20,800（79.27%） |
| FF | 10,759 / 41,600（25.86%） |
| BRAM tile | 8 / 50（16.00%） |
| DSP | 48 / 90（53.33%） |
| DRC errors | 0 |
| BRAM/config guard | PASS（CFGBVS-1、REQP-1839、RBOR-1） |

### 4.1 最差 setup 路径

最终 `route_timing_summary.rpt` 的最差路径为系统时钟域内部路径：

- 起点：`u_soc/i_control/i_activation/state_reg[1]/C`
- 终点：`u_soc/i_control/i_activation/mem_reg_0/ENBWREN`
- 路径类别：同一 50 MHz `clk_out1_clk_wiz_0` 上的 setup 路径
- 数据路径延迟：23.190 ns，其中逻辑 7.209 ns、布线 15.981 ns
- 逻辑级数：33（含 CARRY4 和 LUT2–LUT6）
- 具体逻辑涉及 activation store 状态、NPU gather 地址/掩码及相关条件控制。

这条最差路径不是 request/response CDC mailbox 路径。CDC mailbox 自身的 payload 约束路径在本次路由后均为正 slack；但是整体 50 MHz 时钟仍因该系统时钟域关键路径失败。

### 4.2 关键路径的逐段时序分析

此路径的 setup 预算为一个系统时钟周期 20.000 ns。路由后数据路径传播时间为 23.190 ns，比周期长 3.190 ns；加上报告中的有效时钟偏斜和不确定度后，最终 slack 为 −3.961 ns。该值是 routed setup WNS，不是综合估计值，也不是 CDC payload max-delay slack。

| 路径阶段 | 网表中可见的逻辑/信号 | 观测 | 对时序的含义 |
|---|---|---|---|
| 起点状态 | `activation/state_reg[1]` | 状态寄存器到输出约 0.456 ns，随后扇出 34 | 激活存储状态参与本周期请求/写控制判定 |
| 响应与 gather 选择 | `a_rsp_valid0`、`gather_row`、`gather_addr` | 经过多级 LUT，进入 gather 地址/行选择逻辑 | 同一周期串联响应状态与地址准备 |
| 掩码和连续性计算 | `gather_mask`、`gather_contiguous`、`calc_contiguous` | 使用 8 个 CARRY4 和后续 LUT；中途有 FO=154 的 `perf_events` 路径 | 这是较深的算术/比较/选择逻辑段，包含连续访问判定和掩码形成 |
| 采样与高位地址控制 | `sample_q[95]`、`high_word`、`saved_word0` | gather 结果经采样与控制器/存储高位字选择逻辑 | 地址、数据字和写事务控制在同周期继续组合传播 |
| BRAM 写请求末级 | `sram_we`、`q_addr`、`count`、`wf`、`saved_word0` | 一个名为 `wf` 的网表信号扇出 749；末级再经过 LUT 到 BRAM `ENBWREN` | 末端存在高扇出控制和额外选择级，增加布线延迟及末级到达时间 |

分解数据路径延迟：

| 分量 | 延迟 | 占数据路径比例 |
|---|---:|---:|
| 逻辑 cell 延迟 | 7.209 ns | 31.1% |
| 布线 net 延迟 | 15.981 ns | 68.9% |
| 合计 | 23.190 ns | 100% |

这说明关键问题同时包含深组合逻辑和布线/扇出影响；不能只按 CARRY4 数量判断，也不能只通过简化布尔表达式假定时序会恢复。当前报告没有提供 placement congestion 图或多个最差路径聚类，所以还不能断言 FO=749 的 `wf` 是唯一根因；应在 RTL 改动后重新查看多条 worst paths、扇出和拥塞报告确认。

时钟路径项中，源时钟到达为 −0.757 ns，目标时钟到达为 −1.491 ns，clock skew（含 CPR）为 −0.245 ns；clock uncertainty 为 0.084 ns。它们不是最大贡献项，主要延迟来自 23.190 ns 数据路径，且其中约 69% 是布线延迟。

### 4.3 面向 RTL 修改的定位建议

以下是基于本条路径的优化检查方向，供 RTL 同学评估，不代表已实施或已验证：

1. **检查 gather 元数据的预计算边界。** 查看 `gather_mask`、首地址/行号和连续性判定能否在发射前或上一周期寄存，避免从 response-valid 到 BRAM enable 同周期依次经过多组 mask/contiguous 比较。
2. **拆分到 BRAM 写使能的组合控制链。** 评估将“本周期产生写事务”的条件与 BRAM 端口地址/字选择分级寄存；修改时需保持原有请求/响应延迟、写次数、写掩码及背压语义。
3. **审查高扇出控制。** 重点检查 `wf`（报告中 FO=749）及 `perf_events` 相关网表锥是否能局部化、分组或由已寄存状态驱动。若引入复制寄存器，需保证所有副本的更新/复位条件一致。
4. **比较多条最差 setup 路径与 placement。** 当前只有首条路径可作直接证据；应同时导出前 20–50 条 setup paths、fanout、拥塞和各阶段时序，判断是否为单一控制锥还是整个 activation/gather 区域的布线问题。
5. **每次改动后闭环功能与时序。** 重跑 70 项当前功能回归、CDC 复位专项、Vivado 综合/布线、CDC payload 审计、hold/setup、DRC 与未约束路径检查。流水线改动尤其要确认事务对应关系、性能计数和 JTAG 读回语义。

不建议直接通过降低目标频率、放宽 20 ns 约束或对相关路径设置 false path 来消除本次违例；这些做法会改变验收目标或隐藏真实同步路径问题。

实现证据：

- `build/fpga_main_20261030/summary.md`
- `build/fpga_main_20261030/route_timing_summary.rpt`
- `build/fpga_main_20261030/route_drc.rpt`
- `build/fpga_main_20261030/route_metrics.tsv`
- `build/fpga_main_20261030/route.dcp`

## 5. Bitstream 身份与实板状态

本次 bitstream 已生成：`build/fpga_main_20261030/fpga_top.bit`。SHA-256：

```text
3598BCCF9CEFEEBCF522AEEC827FE6CA25BD7B243DBF971C1DC31E5D346B539B
```

源码快照及 SHA-256 清单见 `build/fpga_main_20261030/provenance.json`。尽管 bitgen 成功，构建摘要状态仍为 `FAILED`，失败原因为最终 setup WNS 为负。因此本轮未将该 bitstream 下载至 EGo1，也没有声称新版本实板功能通过。

## 6. 验证器自身检查

`verification.fpga.test_nocpu_board_suite` 和 `verification.fpga.test_nocpu_evidence_check` 共 8 项单元测试通过。日志中出现的 “bitstream SHA-256 mismatch” 是负向测试的预期输入，用于确认校验器拒绝哈希错误；该信息不代表本轮设计验证失败。

## 7. 复现命令

以下命令在最新 `main` 的隔离 checkout 根目录执行。Vivado 构建目录应为空缺省的新目录；本报告记录的运行已经完成。

```powershell
& 'C:\Users\Lenovo\.conda\envs\ai_env\python.exe' `
  hardware\npu\scripts\run_program_regression.py `
  --output build\cdc_focus_program_regression_20261030 `
  --trained-model software\models\tinycnn8_flatten_strong.npz

& '.\verification\fpga\run_cdc_reset.ps1' `
  -Output '.\build\cdc_reset_latest_main_20261030_host'

& 'C:\Users\Lenovo\.conda\envs\ai_env\python.exe' `
  hardware\fpga\scripts\build_nocpu.py `
  --vivado-root 'D:\AMDDesignTools\2025.2\Vivado' `
  --output build\fpga_main_20261030 --jobs 4 --mhz 50 --implement
```

## 8. 尚未覆盖的项目

- 最新 bitstream 的实际板上下载、DONE/CRC 记录、fabric TAP 检查及模型推理：本轮未运行。
- 示波器测量系统时钟实际频率、TCK 电气波形/线缆传播、物理复位按键、失锁、断电重启、温压角落和长期运行：本轮未运行。
- CDC 状态空间穷尽、形式验证/属性证明、亚稳态 MTBF 分析及代码/功能覆盖率收敛：本轮未运行。
- 仍有 109 条 CDC-15 mailbox 结构告警，保留供设计审查；功能仿真和路径约束审计不能取代协议级形式证明。

**最终判定：** CDC 重点功能仿真和 mailbox 时序路径审计通过；NPU/JTAG 指定回归通过；但最新 `main` 的 50 MHz FPGA setup 时序签核失败，且没有本轮最新镜像的实板测试。因此整个 FPGA 验证不满足完整通过条件。
