# no_cpu 接口级性能观测

性能扩展 v1 在系统时钟域被动记录每次推理的接口活动。使用 ABI 3.0 READ 命令读取，无需 CPU、固件或新增 JTAG 指令。目标运行频率50 MHz。

```powershell
python software/host/nocpu_jtag.py perf --host 127.0.0.1 --port 6666
```

也可调用 `Npu.performance()`，返回包含 job ID 的命名计数字典。该操作不 ACK、不复位、不修改模型。需要配置好的 FT232H/OpenOCD 连接及支持本扩展的 FPGA 镜像。

## 寄存器

`0x68` 是只读布局标识，返回 `0x00010018`（version=1，count=24）；INT4 的 ABI 版本为3.0，性能 bank 布局保持不变。计数 bank 位于 `0x100..0x15c`，每项 32 bits。运行/验证期间读取 bank 返回 busy 错误 4，非对齐读取返回错误 3；`0x160` 不属于 bank。

| 地址 | 名称 | 含义 |
| --- | --- | --- |
| 0x100 | active_cycles | 推理核心非 IDLE 的周期，与原 cycles 一致 |
| 0x104 | model_requests | 模型读请求握手次数 |
| 0x108 | model_stall_cycles | 模型请求 valid 且 ready=0 的周期 |
| 0x10c | model_max_stall | 上述条件最长连续周期 |
| 0x110 | model_responses | 模型读响应次数 |
| 0x114 | model_wait_response_cycles | 有未完成模型请求且当拍没有响应的周期 |
| 0x118 | activation_requests | 激活读请求握手次数 |
| 0x11c | activation_stall_cycles | 激活请求 valid 且 ready=0 的周期 |
| 0x120 | activation_max_stall | 上述条件最长连续周期 |
| 0x124 | activation_responses | 激活读响应次数 |
| 0x128 | activation_wait_response_cycles | 有未完成激活请求且当拍没有响应的周期 |
| 0x12c | writes | 核心写回次数，与原 act_writes 一致 |
| 0x130 | mac_issues | MAC 发射次数，与原 issues 一致 |
| 0x134 | mac_wait_activation_cycles | 结果槽/权重均可用，等待激活 gather 完成 |
| 0x138 | mac_wait_weight_cycles | 结果槽可用，等待目标权重 bank 就绪 |
| 0x13c | mac_wait_slot_cycles | 最后 K 组需要保留的结果槽仍被占用 |
| 0x140 | kernel_prepare_cycles | 有待发射任务时的 kernel offset 准备周期 |
| 0x144 | result_slots_peak | 已保留结果槽的占用峰值，0..4，包含仍在 MAC pipe 中的结果预留 |
| 0x148 | weight_banks_peak | ready 权重 bank 峰值，0..2，不含正在填充的 bank |
| 0x14c | inflight_peak | 两个权重 bank 的在途 MAC issue 总量峰值 |
| 0x150 | post_lanes | 后处理输入 lane 数；量化场景下与最终写回存在流水延迟 |
| 0x154 | gather_contiguous_groups | 非空、地址连续、可打包读取的 gather 组数 |
| 0x158 | gather_scalar_groups | 非空、地址非连续、需要逐 lane gather 的组数 |
| 0x15c | gather_zero_groups | 全 mask=0、无需激活读取的 gather 组数 |

## 计数窗口与一致性

- 接受 START、开始模型验证或核心 RESET 时清零；只统计推理，不含模型验证及主机上传/读结果的开销。
- 事件先采样一拍再计数，降低监测逻辑直接挂在关键路径上的负担。完成状态可读时，计数已稳定；ACK 保留计数，下次 START 重新计数。
- MAC 三种等待原因按“结果槽 → 权重 → 激活”优先级互斥归因，只在 EXECUTE、MAC 模式、有待发射任务且 kernel 准备完成时统计。它们不是全程 stall 的完整划分，也不能全部相加后解释为推理总周期。
- 存储两条读接口各最多一个未完成响应；请求和响应同拍时，未完成标记保留，代表旧响应由新请求替换。
- 同步 BRAM 下一拍即响应时，`wait_response_cycles=0`；它统计额外等待，不是完整读延迟。响应端固定 ready=1，因此没有响应背压计数。写回接口没有 ready 信号，`writes` 仅表示写回活动。
- 32-bit 累计计数按模 2^32 递增。50 MHz 下 2^32 周期约 85.9 s；正常默认 watchdog 为 10,000,000 周期。watchdog 引发的核心 RESET 会清除计数，故故障前历史不保留。
- 对外读请求由原有 JTAG mailbox 跨域，新增计数器不增加直接跨时钟信号。bank 在 owned/busy 期间拒绝读取；主机还核对读取前后的 job ID 和 status。多个主机的命令仍需外部串行化，本扩展不提供独立于 START/RESET 的快照锁。

## 如何解释拥塞与 boundary

`*_stall_cycles` 非零说明接口确实出现背压；`*_max_stall` 表示最坏连续停顿。等待响应计数识别存储延迟；MAC 等待权重/激活则识别调度依赖，即使外部 ready 始终为 1，也可能因带宽或 gather 组装速度不足而等待。

结果槽达到 4 只说明到达容量边界；必须结合 `mac_wait_slot_cycles` 判断它是否阻碍 MAC 发射。权重 bank 峰值和 inflight 峰值同样是容量观测，不能单独视为瓶颈证明。

scalar/zero gather 显示非连续地址与 mask 边界的活动，可能来自 padding、kernel/通道布局、尾组等。它们不是逐一归因的“padding 次数”，也不提供对应算子 PC、地址或时间波形。

这些计数按整次程序聚合。精确到某个算子或某个时刻的定位仍可增加分段计数或 ILA；本版没有插入 ILA。任何新增观测逻辑都必须重新完成 50 MHz 布线时序及实板测试，旧镜像的验证不能代替新镜像。

RTL 回归中的随机存储延迟测试用独立接口 scoreboard 核对请求、响应、累计/最长停顿和额外响应等待；程序回归核对两次推理清零、重复性、原周期/写回指标一致及占用边界。实板最终时序、计数与证据另见本轮验证报告。
