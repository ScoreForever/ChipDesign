# no_cpu FPGA 50 MHz 实板验证（2026-10-24）

本轮在 EGo1 V2.2（`xc7a35tcsg324-1`）上实际执行测试，50 MHz 镜像已下载至 FPGA SRAM。自动化测试通过；物理复位按键、断电重启及电气/温度测试尚未完成，因此结论限于本文列出的条件。

## 修改与时序

当前分支为 `exp/no_cpu`。本轮 RTL 修改位于 `hardware/npu/rtl/npu_program_core.sv`：将激活 BRAM 初始请求地址从 `calc_addr[calc_contiguous ? calc_first : 0]` 改为 `calc_addr[calc_first]`，移除地址选择路径上的连续性判断。

等价条件：连续访问本来就选 `calc_first`；非连续初始请求仅在 `calc_mask[0]` 有效时发出，此时 `calc_first == 0`；其余情况下该地址没有有效请求握手。逐字 gather 地址路径保持原有行为。

修改后 RTL 回归 58 个程序场景通过，另含 20,000 个量化向量、存储背压/复位、非法描述符、生命周期和 JTAG 测试。与原回归共有的 56 个场景，其周期数、写次数和核对的写事件一致。这些是仿真证据。

| 最终布线指标 | 结果 |
| --- | --- |
| 系统时钟周期 | 20.000 ns（50 MHz） |
| Setup WNS / Hold WHS | +0.049 ns / +0.024 ns |
| Pulse-width slack | +3.000 ns |
| Setup / Hold / Pulse-width 失败端点 | 0 / 0 / 0 |
| 未约束内部端点 / 无时钟端点 | 0 / 0 |
| 完全布线网络 / 路由错误 | 14,727 / 0 |
| DRC errors | 0 |
| LUT / FF / BRAM tiles / DSP | 8,563 / 4,495 / 4 / 48 |

初始基线布线 WNS 为 -0.570 ns，经布线后物理优化可达到 +0.064 ns。修改后综合 WNS 从 -1.394 ns 改善至 +0.427 ns，但最终布线 WNS 为 +0.049 ns，**不能据此声称最终布线裕量优于优化后的基线**。当前裕量很小，新增逻辑或修改布局后必须重新验证。

CDC 仍有 109 条 CDC-15 警告，对应握手期间保持的 72-bit 请求和 37-bit 响应 mailbox；两组 toggle 同步和三组复位同步已被工具识别。另有 75 条 DSP 流水线建议及 11 条 methodology 警告（1 条 LUTAR-1、6 条 SYNTH-6、4 条 SYNTH-10）。这些警告已分类，未被视为不存在。LUTAR-1 涉及外部复位与 MMCM locked 组合，实板物理复位和失锁仍需补测。

## 下载身份与实板结果

Vivado 2025.2，配置线缆 `1234-tulA`，配置 IDCODE `0362D093`。下载后 `DONE=1`、`CRC_ERROR=0`。外部 FT232H 序列号 `210512180081`，fabric TAP IDCODE 为 `1`，TCK 接 H16。

Bitstream：`build/fpga_ego1_50mhz_addressfix_impl_20261024/fpga_top_h16_50mhz.bit`。

SHA256：`1200bd566a51d14d7694b5d6ed7764a3e9b4fbb06426a8b01ff7aa4a53909a09`。

最终实板套件 `board_run_02` 正常退出，`passed=true`，耗时 467.828 s：

- 166 次常规数值比对，加上确认 busy 后停止 TCK 的长链输出比对，共 **167 次输出逐字节一致**。
- 25 项错误检测/恢复检查通过，涵盖非法命令、地址/对齐、上传不完整、描述符、量化参数、结果所有权、watchdog 超时及 RESET 恢复。
- 100 kHz 和 1 MHz TCK 均通过：各 32 次 IDCODE、288 次固定寄存器读取、37 组 watchdog 写回比对。
- 确认 busy 后停止 TCK 50 ms，长链运算继续完成且输出正确；确认 busy 后执行协议 RESET，返回 idle 且没有陈旧完成事件。
- 同一模型不复位、不重载，连续 100 次不同输入通过；另有 100 kHz 下的数值运行。
- Conv、FC、ReLU、MaxPool、Requant、融合/非融合、padding/stride、INT32、负数池化、长 K、完整激活区、单 MAC 等用例通过。
- 训练权重用例通过；其中 8 组历史合成输入还与独立的 `tinycnn8_golden.run_model` 核对。它们不是实际语音样本或分类准确率评估。
- TinyCNN 与训练模型的非融合版本超过 4096-byte 激活区，被主机编译器拒绝；这两项不计为实板通过项。

独立归档核验重新读取全部 expected/actual/input 文件，确认字节内容及 SHA256；27,698 条主机 exchange 日志序号连续。OpenOCD 内部执行的长链扫描另存 Tcl 脚本，不包含在该日志条数中。结束后额外实读 ABI=`0x20000`、status=`0`、error=`0`、watchdog=`10000000`，确认 idle。

首轮 `board_run_01` 的失败原因是测试脚本写入 watchdog=1 后仍预期普通 READ 成功；该 watchdog 同时约束命令服务，READ 应超时。修正脚本并增加显式超时/RESET 恢复检查后，完整重跑通过。这一失败没有引出额外 RTL 修改。失败日志与最初 OpenOCD 非特权 USB 访问失败日志均保留。

测试结束已关闭本任务 OpenOCD、解除 FT232H 的 WSL USB 附着并停止本任务 WSL keepalive；保留经过验证的 SRAM 镜像，未写配置 Flash。

## 数据流拥塞与边界的可观测范围

目前实板可读总周期、issue 次数、模型读取数、激活读取数、写次数与 elapsed cycles。相同 CNN 输入的融合版本为 764 cycles / 41 writes / 227 activation reads，非融合版本为 2069 cycles / 423 writes / 609 activation reads。这证明融合减少了中间数据访问与执行周期，但不能单凭这些计数判定哪一个接口拥塞。

若 boundary 指存储容量、padding、尾块或 INT32 等功能边界，本轮已有对应覆盖；若指吞吐瓶颈或模块交界处，尚缺逐接口观测：`valid && !ready` 的累计/最长连续周期、`valid && ready` 的传输次数、缓冲区占用峰值、MAC 等待数据/写回阻塞周期，以及按算子分段的计数。必要时用 ILA 同时捕获这些握手和状态机，区分源端断供与下游背压。应另行实施观测 RTL，并重新完成 50 MHz 时序和实板回归，不能由当前数值正确性测试推断“无拥塞”。

主机 wall time 包含 Python/RPC/JTAG 及结果读取开销，不代表 NPU 核心延迟；分析计算性能应使用板上 cycles 和 20 ns 系统周期。

## 证据与限制

机器摘要：`docs/validation/fpga_board_50mhz_20261024.json`。

| 内容 | 路径 |
| --- | --- |
| RTL 回归 | `build/nocpu_rtl_50mhz_addressfix_20261024/summary.json` |
| 前后共有场景比较 | `build/validation_nocpu_50mhz_20261024/rtl_comparison.json` |
| 源码身份、时序、CDC/DRC 与 bitstream 审查 | `build/fpga_ego1_50mhz_addressfix_impl_20261024/review_gate.json` |
| 时钟、时序与路由 | 同目录 `clocks.rpt`、`route_timing_summary.rpt`、`route_status.rpt` |
| 下载证据 | `build/validation_nocpu_50mhz_20261024/program/console.log` |
| 实板结果与字节/命令证据 | `build/validation_nocpu_50mhz_20261024/board_run_02/` |
| 归档核验与最终寄存器 | 同目录 `audit.json` |
| 测试脚本 | `build/validation_nocpu_50mhz_20261024/board_suite.py` |

50 MHz 依据指定的板载 100 MHz 晶振及 routed MMCM 20 ns 生成时钟，未用示波器测频。FT232H IO 时序采用已记录的线缆/板级延迟假设，未实测电气时序。未执行物理按键复位、断电重启、电压/温度角落及长期老化。协议 RESET 测试不能代替物理复位测试。当前结论不构成这些项目通过，也不构成所有数据流边界已无拥塞的证明。
