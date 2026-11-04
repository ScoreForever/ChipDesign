# 逐算子统计与片上波形采集

本扩展使用系统时钟域的被动采集缓冲，通过现有 fabric JTAG 导出 VCD、CSV 和 JSON。它不是 Xilinx ILA IP，不需要新增调试核或使用配置线缆传输采样数据。原有 24 项整次推理计数继续可用。

两类数据同时保留：

- 最多 16 个硬件算子的完整起止时间、描述符、MAC 发射数，以及等待激活、权重和结果槽的周期数。
- 选定一个硬件算子的连续 1024 周期、每周期 128-bit 波形。可设置相对算子起点的周期偏移，或等到该算子首次出现结果槽阻塞时开始采集。

默认选择 PC=0、offset=0、启用采集。50 MHz 下 1024 周期为 20.48 μs。较长算子会明确标记截断；其逐算子汇总仍完整。可在下一次推理选择其他 PC 或更晚的 offset，采集另一段波形。

## 使用

在 FPGA 新镜像已下载、FT232H/OpenOCD 已连接时：

```powershell
python software/host/nocpu_jtag.py trace-config --pc 1 --offset 0
python software/host/nocpu_jtag.py infer --package <package_dir> --input <input.bin> --output <output.bin>
python software/host/nocpu_jtag.py trace --package <package_dir> --output build/operator1.vcd
```

最后一步同时生成 `operator1.vcd`、`operator1.csv` 和 `operator1.json`。VCD 可用 GTKWave 等工具打开，JSON 包含全部算子汇总、原始采样字和采集配置。`--package` 用于按已检查的算子数量和描述符标注 manifest 中的逻辑算子组；它不核验 FPGA 中的全部权重内容。

`trace-config --trigger-slot` 在达到 offset 后等待 `wait_slot`，触发后继续记录该算子的每个周期；`--disable` 关闭波形写入，但保留逐算子汇总。`Npu.configure_trace()`、`Npu.trace(include_samples=True/False)` 提供对应 Python API。配置作用于下次推理，验证/装载模型不会重置配置。

硬件 PC 指编译后的描述符，融合的 Conv/Requant/ReLU/Pool 可能属于同一个 PC。查看 package 的 `groups` 或导出 JSON 的 `logical_ops` 获取对应关系。

## 保留与一致性

- START、开始模型验证或核心 RESET 清除上次采集元数据和逐算子汇总；RAM 内容不复位，但未采集的地址不可读取。
- 正常完成后冻结，ACK 保留波形和汇总。运行/验证期间读取 trace 返回 busy 错误 4；主机还核对读取前后的 job ID/status。
- 配置写入遵守原有所有权规则：运行时错误 4，结果尚未 ACK 时错误 9。正常 `infer` 会 ACK，随后可读波形或配置下一次采集。
- 协议 RESET 恢复默认配置并清除采集。watchdog 引发的核心 RESET 同样清除采集，不能靠本版恢复故障前波形。
- 配置寄存器反映下一次设置；`captured_control/captured_offset` 是当前采集的配置快照，即使之后改了设置仍可正确解释旧波形。
- 读取没有隐式 ACK、RESET 或执行恢复。多个主机仍需串行化访问，不存在独立快照锁。

## Trace 寄存器

使用 ABI 3.0 的 READ/WRITE，寄存器字节地址、4-byte 对齐。ASIC 默认关闭波形 RAM 和逐算子摘要；全局性能计数仍在。

| 地址 | 内容 |
| --- | --- |
| 0x180 | 布局标识 `0x00030080`，version 3，128-bit sample |
| 0x184 | 深度 1024；未综合波形 RAM 时为0 |
| 0x188 | 配置：bit0 enable、bit1 wait-slot trigger、bits7:4 PC；其余位保留为 0 |
| 0x18c | 相对选定算子开始的周期 offset |
| 0x190 | 状态：bit0 started、bit1 full、bit2 job finished、bit3 truncated |
| 0x194 | 已采集样本数，0..1024 |
| 0x198 / 0x19c | 首/末样本在整次推理中的绝对周期 |
| 0x1a0 / 0x1a4 | 样本/算子汇总步长：16 / 32 bytes |
| 0x1a8 | 算子汇总条目数：16；未综合逐算子摘要时为 0（ASIC 默认） |
| 0x1ac / 0x1b0 | 本次采集的 control / offset 快照 |
| 0x200..0x3ff | 算子汇总，PC × 32 bytes |
| 0x1000..0x4fff | 样本 RAM，index × 16 bytes |

只有 0x188、0x18c 可写；非法配置保留位返回参数错误 8。未采集样本或越界地址返回地址错误 2，非对齐返回错误 3。

每个算子的 8 个 32-bit 汇总字依次为：start cycle、end cycle（右端不含）、完整描述符 word0、flags、等待激活、等待权重、等待结果槽、MAC issues。flags bit0=已开始，bit1=正常完成，bit2=失败，bit3=描述符有效；正常算子为 `0xb`。时间包含该描述符的 fetch、参数装载、执行和 drain。正常程序的相邻区间首尾相接，总长度等于原 cycles。

MAC 三类等待沿用整次计数的“结果槽 → 权重 → 激活”互斥归因。算子汇总各列之和对应整次推理的三个等待计数与 MAC issues。

## 128-bit 样本格式

所有值在系统时钟上升沿之前采样，再写入同步 BRAM。未出现有效请求/写回时，对应地址和写入 nibble 数归零。

| 32-bit 字 | 位域 |
| --- | --- |
| word0 | 整次推理中的 cycle |
| word1 bits0..11 | model valid、model ready、model response、activation valid、activation ready、activation response、write valid、MAC issue、wait activation、wait weight、wait slot、kernel prepare |
| word1 bits15:12 / 19:16 | slot busy / slot ready，均为 4-bit mask |
| word1 bits21:20 / 27:22 | 权重 bank ready mask / 总 inflight issue 数 |
| word1 bits29:28 / 31:30 | gather state / weight load state |
| word2 bits15:0 / 31:16 | model byte 地址 / activation nibble 地址 |
| word3 bits15:0 / 19:16 | write nibble 地址 / 写入 nibble 数（0=无，1=INT4，2=奇数张量末元素及padding，8=INT32） |
| word3 bits23:20 / 27:24 | 核心 state / 描述符 PC |
| word3 bits30:28 / 31 | opcode / quantized；fetch 期间记为 0 |

核心 state：0 IDLE、1 FREQ、2 FWAIT、3 BASIC、4 CHECK、5 PREQ、6 PWAIT、7 SETUP、8 EXECUTE、9 NEXT、10 FAILED、11 INIT_A、12 INIT_B、14 DERIVE。

VCD 的 clock 是根据声明的采样周期重建的显示时钟，默认 20 ns；它不是示波器测量。数据来自 FPGA 上逐周期采集，两个采样沿之间的毛刺不可见。VCD 在窗口结束时将数据标为未知，避免把采集结束后的状态误看成实际保持不变。本版不记录 MAC/量化内部数据或 write payload。

## 验证

`tb_npu_trace.sv` 独立核对原始字、边界、偏移、触发、截断、失败、关闭、复位及未采集 RAM 保护。程序回归核对每个描述符的区间连续性、flags/描述符身份、分项计数加总、重复推理清零，以及直接/JTAG 两种传输的同步 RAM 读取。实板报告另记录实际导出的 VCD、波形逐拍核对、资源和 50 MHz 最终时序。
