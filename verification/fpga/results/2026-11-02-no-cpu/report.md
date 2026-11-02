# no_cpu / ABI 2.1 FPGA 50 MHz 验证报告

日期：2026-11-02（Asia/Singapore）。对象为 `origin/exp/no_cpu`，提交 `175484e63073b314c1f2465c06c5a921d6673e4a`，INT8/Q31、ABI 2.1。此报告独立于 no_cpu_INT4 / ABI 3.0，不沿用其功能或实板结果。

**结论：本报告列出的自动项目全部通过；no_cpu 物理按键、断电重下载恢复和电气波形尚未覆盖。** RTL、50 MHz routed timing、CDC 动态/结构审查、实板完整功能套件、训练权重、停钟/协议复位恢复均有本次独立证据。物理操作等待现场确认，未引用 INT4 的物理测试代替本版本。

## 版本与可追溯性

原工作区有其他 ASIC 流程修改，因此在 `build/no_cpu_validate_20261102` 创建独立分支 `codex/fpga-nocpu-validation-20261102`。初始远端为 `fea96a2`；验证期间上游新增 ASIC CDC 流程提交 `175484e`，验证分支已快进。该提交不涉及 FPGA RTL、XDC 或构建入口，构建结束后逐项复核 `provenance.json`，当前全部输入 SHA-256 匹配，无失配。

本次没有修改设计 RTL、目标时钟或 FPGA 约束；原分支已包含 10 月 31 日的 INT8 时序修复。新增/调整的是实际 TAP 测试、ABI 2.1 实板恢复脚本，以及原证据检查器缺失的 `tiny_strided_asymmetric` fixture。原工作区其他文件不属于本次验证输入。

Vivado 2025.2，目标 EGo1 / `xc7a35tcsg324-1`；100 MHz 输入、MMCM 50 MHz 系统时钟（20ns）；fabric TCK 1MHz（1000ns）。本次 bitstream SHA-256：`60465500BD464C7161DE9F8D1DC7F4203EE5A2FB07E1EE84453CBDCC110969E9`。完整 bit/dcp 在本地构建目录保留，报告归档保存原始工具报告、源码哈希、编程记录与输出证据。

## 功能仿真

- 完整原有回归 72/72，通过 4×8、4×4、融合/非融合、实际 JTAG transport、Conv/FC/MaxPool/ReLU/Requant、INT32 输入、边界/非法描述符、生命周期、abort、timeout 和 incomplete 检查。
- 另在已编译 4×8、4×4 RTL 镜像上分别执行训练权重 fused 模型，通过 2/2；合计 74 项。训练输入为确定性合成数据，验证数值执行，不表示数据集分类准确率。
- 20,000 个 Q31 量化向量、双 SRAM 行为语义、trace、随机读写 stall 与 drain 通过。此处 FPGA 使用 BRAM 推断，没有声称重跑外部 ASIC SRAM 宏模型。
- host transport/performance 和 trace 单元检查各 7 项通过；board-suite 和 evidence-checker 离线检查各 4 项通过。检查器新增了当前 RTL fixtures 已有的非对称 stride 用例，避免把额外正常 fixture 误报为未知。

## 时序与资源

| 指标 | 本次 routed 结果 |
|---|---:|
| setup WNS / TNS | +0.064ns / 0.000ns |
| setup 失败 / 总端点 | 0 / 38329 |
| hold WHS / THS | +0.035ns / 0.000ns |
| hold 失败 / 总端点 | 0 / 38220 |
| pulse-width 最小余量 / 失败端点 | +3.000ns / 0 |
| LUT | 16564 / 20800，79.63% |
| FF | 10735 / 41600，25.81% |
| BRAM36 tile | 8 / 50，16.00% |
| DSP | 48 / 90，53.33% |

### setup 最差路径

`i_npu/issue_p_reg[1]/C` → `i_activation/cache_data_reg[7][7]/D`，均为 50MHz 系统域，24 级组合逻辑。数据路径 19.534ns，其中逻辑 5.223ns（26.738%）、布线 14.311ns（73.262%）；clock skew −0.035ns，uncertainty 0.084ns，终点 setup 0.283ns。因此余量为 `20 − 19.534 − 0.035 − 0.084 − 0.283 = 0.064ns`。

这条路径经过 gather/activation 访问控制及写使能逻辑。`i_controller/wf` 扇出 749，该网线在此路径中贡献约 1.780ns；其前还有扇出 93、延迟约 1.001ns 的共享控制网。最差 50 条路径全部从 `issue_p[1]` 发出，终点为 16 个 D、34 个 CE，说明关键范围是一组访问/写控制路径。网表中 `perf_events`、`sample_q` 等名称可能是共享优化逻辑，不能只凭网名归因于性能计数器或 trace。

本次满足 50MHz，但仅有 64ps 最差 setup 余量，不构成更高频率、多实现 seed 或其他器件的保证。后续改动应重点复核 gather→activation 控制深度、写使能扇出与物理分布，并重新执行完整时序门禁。历史 main 报告 −3.961ns 与本次 +0.064ns 相差 +4.025ns；它是不同提交的历史对照，本轮未新增 RTL 时序修复。

### hold、外部 I/O 和未约束项

最差 hold 为 trace `sample_q_reg[124]/C` → `g_wave.ram3_reg/DIADI[28]`，数据路径 0.248ns（逻辑 0.141ns、布线 0.107ns），余量 +0.035ns；不是 setup 的最差路径。

无 no_clock、unconstrained_internal_endpoints、multiple_clock、组合或 latch loop。TDI/TMS 均有双边沿等效输入预算；TDO launch 于下降沿、FT232H 在上升沿采样。外部审计最小 setup/hold 余量：TDI 467.319/472.623ns，TMS 467.628/472.539ns，TDO 447.635/483.398ns。其成立依赖 `docs/JTAG_IO_TIMING.md` 的短线、1MHz、板/线延迟假设，没有用仪器复测线路。

TIMING-18 的一个缺省输入延迟告警来自异步 `rst_ni`；LED 输出无同步接收端，设置了专用输出 false-path。没有用全时钟组 false-path 切断 payload 路径或同步器第二级。

## CDC 专项

实际 `npu_jtag_transport` 重新运行独立仿真：100 组 TCK 速率/相位组合、命令背压、重复提交/overrun、停 TCK 后响应保持、TMS reset 不取消事务、三个阶段的停钟异步复位、恢复后新事务，全部通过。该模块在两版本中源码相同，但本次独立编译并执行，没有把 INT4 结果当作 no_cpu 的通过证据。

原始 Vivado CDC：CDC-3 info 2 条、CDC-9 info 3 条、CDC-15 warning 109 条。它们逐条匹配 72 位请求邮箱和 37 位响应邮箱的 CE 控制采样路径；raw warning 保留，未新增 waiver。

| CDC 路径 | 有效物理约束与最小余量 |
|---|---|
| request payload | 72/72 路径，20ns max-delay，+18.061ns |
| response payload | 37/37 路径，20ns max-delay，+18.063ns |
| request toggle 第二级 | setup +18.788ns，hold +0.270ns |
| response toggle 第二级 | setup +998.907ns，hold +0.245ns |

两侧 mailbox 在握手完成前保持数据；toggle 用两级 ASYNC_REG，同步器两级间无组合逻辑，第二级 setup/hold 仍被计时。异步入口例外只到第一级 D，复位例外只到同步器 CLR。严格检查器核对端点对、路径数、约束和时序；未知或变化的警告会失败。数字仿真与功能实板均不能证明模拟亚稳态 MTBF。

## 工具告警保留

route DRC 无 error，保留 DSP pipeline 提示 76 条（DPIP-1 46、DPOP-1 9、DPOP-2 21）；配置/BRAM 专项 DRC 零违规。route methodology 14 条 warning：LUTAR-1 1、SYNTH-6 8、SYNTH-10 4、TIMING-18 1。LUTAR 对应 reset 条件合成后的 LUT，复位释放已同步，但本轮没有宣称电气毛刺被实测排除；RAM 输出寄存器合并和宽乘法器提示亦保留。

## 实板功能与训练权重

已通过 Xilinx/1234-tulA 下载本次镜像至 xc7a35t_0，DONE=1、CRC=0。外置 FT232H 0403:6014 / 210512180081，fabric TAP IDCODE=0x00000001，TCK 1MHz。

完整实板套件：35 项硬件 PASS，每项两组输入逐字节比较；1 项 tinycnn 非融合模型因超出激活容量按预期在主机拒绝，未发送硬件；14 项协议负向/恢复检查全部 PASS。覆盖五算子、融合/非融合、INT32 输入、长 K、非对称 stride/padding、全 8KiB 激活区、高地址和结果槽压力。独立证据检查器确认全部输入/期望/实际输出及哈希，保留原始报告的 manual_pending，专项追加结果另行记录，不修改历史原始 JSON 来冒充覆盖。

训练权重模型另做两轮输入（固定伪随机及逆序），均与整数参考一致。NPZ SHA-256 为 `1732c470ec65f2d2c0ebc96dd93326dabb058277b71eef1bb3717d2ddee2f83a`。每轮读取 24 项 performance bank 和 per-operator trace，算子 MAC issue 合计与总计相符。4×8 实板每轮 active_cycles=3262、writes=804、mac_issues=2440；周期和写回数与本次 4×8 RTL 训练模型仿真精确一致。4×4 RTL 为 5886 周期，未把它算作实板阵列配置覆盖。训练模型用例读取算子摘要与波形头，不宣称已经导出其完整 1024 条 waveform sample；这些内部数字信息也不代表外部电气波形。

另在协议 RESET 后执行新的 FC smoke，输出 `fbffffff0c000000dcffffff` 与参考一致；从 FPGA trace RAM 实际读取并解码 64 条内部数字采样，检查首末时间戳、相邻周期连续和 PC 匹配，全部通过。此检查明确标为 `reset_scope=protocol`，job 计数保留 8806，不能据此声称全局/物理复位通过。原始采样见 `evidence/internal_trace/trace.json`。

## 实板 CDC 与协议恢复

第一轮停 TCK 后完整输出匹配；后续重复 START 检查因在读取状态前任务已完成而未覆盖 busy。原始失败报告保留。脚本增加最多五次显式窗口重试，只对“状态已 done”或重复 START 返回 result-pending 认定窗口错过，其余错误直接失败。第二轮完整专项通过，`window_misses=[]`，没有实际使用重试。

| 专项 | 第二轮实测 |
|---|---|
| 确认 busy 后停 TCK 1 秒 | 0x03 → 0x0a，恢复读取的全部输出匹配软件参考 |
| busy 时重复 START | 最后有效状态 0x03，重复命令返回 error=4 |
| busy 时协议 RESET | 最后有效状态 0x03；RESET 后 busy/model/input ready/done/fault/error 清零 |
| watchdog 超时和恢复 | 设置 1000 周期，faulted=true、error=10；RESET 后状态清零 |
| 恢复后完整推理 | 重新加载同一工作负载，完整输出匹配参考 |

专项使用 64×64×1 输入、16 层 Conv3×3 + Requant；ABI 2.1 Q31 identity multiplier=0x40000000、shift=1，未沿用 INT4 Q15 参数。停钟证据是 target-less OpenOCD 在暂停期间不发送任何 scan/runtest RPC，未以示波器验证 TCK 电平，也不等同于 Shift-DR 中途暂停。

## 物理按键、断电与测量范围

已准备 ABI 2.1 专用连续运算工作负载：16 通道、16 层 Conv3×3 + Requant，模型内复用相同只读权重/参数块。按键前已完成一次全部 1936 字节输出的 golden 比较，通过。等待窗口内连续运行，最终记录 job ID 8806、无 fault/error；该 ID 是累计计数，不能当作本窗口执行次数。未收到按键操作确认，180 秒窗口到期，原始报告 `passed=false`、原因 `operator window expired without reset`，属于未覆盖，不能签成物理复位通过。

窗口结束后执行协议 RESET 清理，保存 idle/无错误状态。此清理不是物理复位证据。现场可用后需重开窗口，收到操作确认并取得最后 busy 采样→复位状态清零的证据，再运行新推理；采样测试仍不能代替边沿级电气捕获。

| 范围 | 当前状态 | 原因/后续 |
|---|---|---|
| no_cpu busy 时 RST/S6 | NOT COVERED | 未收到现场操作确认；负载和监测脚本已准备并验证 |
| no_cpu 断电、重上电、重下载恢复 | NOT RUN | 等待现场断电/接电操作；不得套用 INT4 的断电证据 |
| Flash 自动加载当前镜像 | NOT RUN | 没有写入 Flash；常规验证只配置 FPGA SRAM |
| TCK/复位/电源电气波形 | NOT RUN | 现场没有示波器/逻辑分析仪；内部数字 trace 不能替代 |
| 温压/多 seed/长期老化/MTBF | NOT RUN | 本轮功能与指定实现验收不构成这些范围的签核 |

## 复现与证据

```powershell
python hardware/npu/scripts/run_program_regression.py --output build/nocpu_rtl_fresh --trained-model software/models/tinycnn8_flatten_strong.npz
python hardware/fpga/scripts/build_nocpu.py --vivado-root D:/AMDDesignTools/2025.2/Vivado --output build/nocpu_50mhz_fresh --jobs 4 --mhz 50 --implement
python verification/fpga/run_npu_jtag_cdc.py --output build/nocpu_cdc_fresh --static-evidence build/nocpu_50mhz_fresh
# 现场人员准备好后开启窗口；运行期间按 RST/S6。
python verification/fpga/nocpu_busy_button.py --output build/nocpu_busy_button_fresh --seconds 600
# 真正物理复位或重新上电并下载后，核对全局初始状态、新推理与内部数字 trace。
python verification/fpga/nocpu_recovery_smoke.py --output build/nocpu_recovery_fresh
```

各输出目录需尚不存在。上板前必须确认同次构建 Result=PASS、bitstream SHA-256、器件及源哈希；不能用 bitstream 已生成替代 timing 验收。归档保存原始时序/CDC/DRC、构建 provenance、编程记录、板测输入/期望/实际输出及日志；本地 .bit/.dcp 不纳入 Git 证据归档。`evidence/source_snapshot` 保存实际 Vivado 输入的原始字节，以便复核 provenance（跨平台换行转换也会改变源码哈希）；`evidence/test_scripts` 保存测试工具源码快照。

主要入口：`coverage.json` 为按范围汇总，`evidence/implementation/summary.md` 与 timing/CDC/DRC 为物理报告，`evidence/rtl/` 为 72+2 项 RTL 证据，`evidence/board_suite/` 为 35 项硬件与 14 项负向测试，`evidence/trained_board/` 为两轮训练权重和计数/trace，`evidence/nocpu_board_cdc_20261102_r2/` 为最终自动恢复专项，`evidence/busy_button_window_expired/` 为尚未覆盖的物理窗口记录。大日志使用无损 gzip 归档。`evidence_sha256.json` 对归档文件做完整性校验。
