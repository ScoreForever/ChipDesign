# no_cpu 接口级观测：50 MHz 实板验证

2026-10-24 在 EGo1 V2.2（`xc7a35tcsg324-1`）完成带性能计数器的新镜像验证。新增 24 项被动计数，通过原有 JTAG READ 读取；没有向执行调度或存储接口添加监测反馈，也未插入 ILA。

## 实际捕获的阻塞

| 实板用例（首次输入） | 总周期 | MAC 等待激活 | MAC 等待权重 | MAC 等待结果槽 | 结果槽峰值 |
| --- | ---: | ---: | ---: | ---: | ---: |
| CNN 融合 | 764 | 147 | 161 | 0 | 4 |
| CNN 非融合 | 2069 | 146 | 162 | 82 | 4 |
| 结果槽压力，融合 | 626 | 0 | 9 | 426 | 4 |
| 结果槽压力，非融合 | 1874 | 0 | 9 | 426 | 4 |
| 训练权重，融合 | 3442 | 148 | 339 | 0 | 4 |

压力用例使用 8×8×1 输入、1×1 Conv、8 个输出通道和 Requant。结果生产快于逐 lane 后处理，4 个结果槽到达容量上限，MAC 因等待槽位停顿 **426 周期**；融合用例总执行时间为 626 周期，即 12.52 μs，其中结果槽等待为 8.52 μs。两条存储读接口的 ready 背压计数均为零，说明这里观测到的是内部结果槽/后处理消费速度造成的阻塞。

CNN 融合和训练权重用例同样达到 4 槽峰值，但没有 MAC 等槽停顿。因此，达到容量上限本身不能判定瓶颈；需要联合等待周期判断。三种 MAC 等待按“槽 → 权重 → 激活”互斥归因，并非整次推理周期的完整划分。

全部 170 组常规数值运行的命名计数见 `docs/validation/nocpu_performance_50mhz_20261024.csv`。主机 wall time 包含 RPC/JTAG、输出读取和计数读取，性能分析使用板上周期数与 20 ns 周期。

## RTL 与软件接口

- `hardware/npu/rtl/npu_program_core.sv`：采样请求握手、ready 背压、响应等待、MAC 等待原因、结果槽/权重 bank/在途 issue 峰值，以及连续/逐 lane/全零 mask gather 次数。事件采样后一拍计数，减少对功能路径的直接负载。
- `hardware/soc/rtl/control/npu_control_subsystem.sv`：连接性能 bank。
- `hardware/soc/rtl/control/npu_command_controller.sv`：`0x68` 返回布局标识 `0x00010018`，`0x100..0x15c` 提供 24 个只读 32-bit 计数；owned/busy 时拒绝读取 bank，防止读取变化中的整组数据。
- `software/host/nocpu_jtag.py`：新增 `Npu.performance()` 和 `perf` 命令，读取前后核对 job/status；正常读取不 ACK、不 RESET。

接受 START、开始验证或核心 RESET 时清零；完成后冻结，ACK 保留。原有 ABI 2 和寄存器保持兼容。完整地址表、计数定义、复位/溢出语义及解释方法见 `docs/NPU_PERFORMANCE_COUNTERS.md`。

在 FT232H/OpenOCD 重新连接后可执行：

```powershell
python software/host/nocpu_jtag.py perf --host 127.0.0.1 --port 6666
```

本次测试结束执行了协议 RESET，当前板卡 idle、error=0，计数已清零；本轮非零测量值保存在 CSV/JSON 中。下一次推理完成后可直接读取新的计数，ACK 不会清除它们。

## 验证结果

- RTL 回归 **62 个程序场景通过**，含 COLS=4/8、直接命令及 JTAG、融合/非融合、训练权重和新结果槽压力用例。原有 58 个共有场景的 cycles、writes、核对写事件完全一致。
- 另含 20,000 个量化向量、存储背压/复位和随机延迟测试。独立接口 scoreboard 核对请求、响应、累计停顿、最长连续停顿和额外响应等待；随机测试中两条接口均实际产生停顿。运行期间读取 bank 的拒绝行为也在 RTL 命令回归中检查。
- 性能 API 4 项、原有 JTAG 编解码 3 项主机测试通过。
- 新镜像实际 SRAM 下载后 `DONE=1`、`CRC_ERROR=0`。实板最终完整运行 `board_run_04` 正常退出，`passed=true`，耗时 **232.785 s**。
- **170 次常规输出比对 + 1 次停止 TCK 长链输出比对，共 171 次逐字节一致**；**28 项错误/恢复检查通过**。每次常规运行还检查新计数与原 cycles/issues/读写计数一致、完成后两次读取相同、ACK 后保留以及 START 清零后的重复性。
- 结果槽压力用例的四次实板运行均达到峰值 4 且确实产生 MAC 等槽停顿。训练权重的 8 组历史合成输入还与独立 `tinycnn8_golden.run_model` 比对；它们不是实际语音准确率测试。
- TCK=100 kHz / 1 MHz 通信、低速数值运行、观察 busy 后停 TCK 50 ms、观察 busy 后协议 RESET、运行中读取 bank 返回错误 4、连续 100 次不同输入均通过。
- 全部输出/输入文件的字节内容和 SHA256 已重新核对；44,931 条主机 exchange 日志序号连续。OpenOCD 内部的长链扫描另存 Tcl 脚本，不计入此日志条数。

此前三个实板尝试因脚本假定在串行 USB/JTAG 查询之间任务持续 busy 而失败：计数读取时任务已完成，或 RESET 检查前已读到 done。日志保留在 `board_run_01..03`。随后将运行中 bank 读取拆成独立任务，并对错过 busy 窗口的观测设置最多 5 次重试，只有实际观察到 busy 才计覆盖。最终 `board_run_04` 的三项窗口检查均在第 1 次尝试通过，没有重试；不需要修改 RTL 或降低工作频率来处理这些脚本时序假设。

TinyCNN 和训练模型的非融合版本仍因 4096-byte 激活区容量被主机编译器拒绝，这两项不计为实板通过用例。

## 新镜像的时序与身份

| 指标 | 最终结果 |
| --- | --- |
| Routed 系统时钟 | 20.000 ns，50 MHz |
| Setup WNS / Hold WHS | +0.826 ns / +0.047 ns |
| Pulse-width slack | +3.000 ns |
| Setup / Hold / Pulse-width 失败端点 | 0 / 0 / 0 |
| 未约束内部端点 / 无时钟端点 | 0 / 0 |
| 完全布线网络 / 路由错误 / DRC errors | 15,630 / 0 / 0 |
| LUT / FF / BRAM tiles / DSP | 8,813 / 5,275 / 4 / 48 |

与上一轮地址优化镜像相比，最终资源增加 250 LUT、780 FF，BRAM/DSP 不变。这是本次实际实现结果；下一次 RTL 或观测改动仍需重新验证时序。

CDC 仍保留已分类的 109 条 mailbox CDC-15 警告；另有 75 条 DSP 流水线建议及 11 条 methodology 警告（含复位组合 LUTAR-1）。完整审查见实现目录 `review_gate.json`。

Bitstream：`build/fpga_ego1_50mhz_perf_impl_20261024/fpga_top_h16_50mhz.bit`。

SHA256：`fc822847bd631cf7877e3fcababb0826690cabb0f9223e5656951bfdda6ffd07`。

配置设备 IDCODE `0362D093`、线缆 `1234-tulA`；fabric TAP IDCODE `1`，FT232H 序列号 `210512180081`，TCK 接 H16。源码哈希、routed checkpoint 与 bitstream 身份已匹配核验。未写配置 Flash。

## 证据与限制

| 内容 | 路径 |
| --- | --- |
| 机器摘要 | `docs/validation/nocpu_performance_50mhz_20261024.json` |
| 完整性能表 | `docs/validation/nocpu_performance_50mhz_20261024.csv` |
| 最终 RTL 回归 | `build/nocpu_perf_rtl_final_20261024/summary.json` |
| 实现、时序、CDC、DRC、源码身份 | `build/fpga_ego1_50mhz_perf_impl_20261024/` |
| 下载记录 | `build/validation_nocpu_perf_20261024/program/console.log` |
| 实板结果、命令、输入和输出 | `build/validation_nocpu_perf_20261024/board_run_04/` |
| 文件复核和最终寄存器 | 同目录 `audit.json` |
| 实板套件 | `build/validation_nocpu_perf_20261024/board_suite.py` |

测试完成已关闭本任务 OpenOCD、解除 FT232H 的 WSL USB 附着并停止本任务 keepalive，保留已验证的 50 MHz SRAM 镜像。

计数按整次程序聚合，尚无逐算子 PC、地址/时间波形或 ILA；watchdog/core RESET 会清除故障前计数。50 MHz 基于板载晶振规格与 MMCM 配置，未进行示波器测频、JTAG 电气时序测量、物理复位按键、断电重启、电压/温度角落或长期老化测试。协议 RESET 不能代替物理复位测试。
