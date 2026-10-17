# 无 CPU 的 JTAG 主机控制系统

日期：2026-10-17；分支：`exp/no_cpu`。本文描述本分支的默认实现。
旧 CV32E40P 系统保留为 `my_soc_cpu_reference`，不进入默认 FPGA 源文件列表。

## 实现范围与结构

```text
PC / 外部 MCU：音频前处理、输入量化、可选 logit 尺度统一和分类
  -> OpenOCD jtag-only / Tcl RPC
  -> FPGA 外接 JTAG 引脚：npu_jtag_transport
  -> cmd/rsp ready-valid：npu_command_controller
  -> 原生装载、任务、调试端口：npu_control_subsystem
  -> tinycnn8_npu_fused（仍自行调度完整固定网络）
```

活动设计移除 CPU、取指/数据桥、AXI crossbar、RISC-V Debug Module、BootRAM、
共享主 SRAM 和 DMA。模型和输入直接写入 NPU 本地存储，因此无需先经共享 SRAM
再 DMA 复制。NPU 的权重、激活、参数和计算阵列保留。

当前网络仍为固定 TinyCNN-8-Flat；本改动不实现可编程算子拓扑。模块编程请求
已经有独立的扩展接口和超时/背压语义，未来可以连接模块寄存器或 descriptor 后端。
当前顶层关闭该功能，明确返回 `UNSUPPORTED`。

| 用户要求 | 实现 |
|---|---|
| 主机命令 | 读写、开始/提交模型、参数提交、开始/提交输入、启动、确认结果、清错、软复位 |
| 模型更新和复用 | MODEL_BEGIN 立即取消旧模型有效性；完整提交后才可运行；跨任务保留模型 |
| 输入搬运 | 80 个 little-endian packed word；内部逐字节写 320 B，写完才回复 |
| 任务控制 | 检查模型、输入、结果所有权、故障和 NPU ready；输入在启动时消费 |
| 背压和所有权 | 单 outstanding；响应保持到接收；忙时不能改内存；未 ACK 的结果阻止新任务 |
| 结果和通知 | 独立 8×INT32 快照、sticky done、任务序号；irq 为 done/fault 的锁存电平 |
| 异常恢复 | 校验完整地址和对齐、传输顺序、参数、命令、状态；任务及扩展事务 watchdog；软复位 |
| 可观测性 | ABI、能力、模型 ID、加载计数、提交 mask、错误地址、任务与核心周期、分阶段计数 |
| 空闲只读查询 | 读取 NPU 实际权重、激活、已提交参数和 live logits；不维护镜像内存 |

`irq_o` 在系统顶层提供给后续集成使用，当前 `fpga_top` 未额外引出中断引脚。
JTAG 主机通过 STATUS 的 sticky done/fault 查询完成通知；JTAG 没有主动推送通道。

## 代码入口

- `hardware/soc/rtl/control/npu_command_controller.sv`：独立于传输的生命周期控制器。
- `hardware/soc/rtl/control/npu_jtag_transport.sv`：专用 TAP、单请求 CDC 邮箱。
- `hardware/soc/rtl/control/npu_control_subsystem.sv`：融合核后端适配与模块扩展端口。
- `hardware/soc/rtl/my_soc_top.sv`：默认无 CPU 顶层。
- `hardware/soc/sim/filelists/chipdesign_nocpu.f`：默认综合/仿真源文件，不包含 CPU/AXI/DM。
- `software/host/nocpu_jtag.py`：OpenOCD Tcl RPC 客户端、部署包和向量装载、推理 API。

融合核增加默认关闭的 `DEBUG_READBACK` 可选接口。查询激活复用已有 bank 读端口，
查询参数和权重直接复用真实数组。旧 MMIO/独立核实例的默认行为不变。
同时把无参数 task 调用改为 `begin_layer;`，兼容本机旧 Icarus，不改变调度。

## JTAG 协议（ABI 1.0）

这是 FPGA fabric 引脚上的专用 host TAP，**不是**原来的 RISC-V Debug Module，
也不是 FPGA 下载电缆连接的配置 TAP。物理引脚沿用已有 `tck_i/tms_i/td_i/td_o`。
不能对它运行 RISC-V halt/resume、SBA 或固件下载命令。

IR 长 5 bit：`0x01` 为 32-bit IDCODE `0x00000001`；`0x12` 为 72-bit 命令；
其他 IR 是单 bit BYPASS。所有扫描 LSB first，TDO 在 TCK 下降沿更新。

| 请求位 | 内容 |
|---|---|
| `[71:64]` | opcode；0 是 NOP，仅查询已有响应 |
| `[63:32]` | 完整 32-bit **字节地址** |
| `[31:0]` | 写入数据或命令参数 |

| 响应位 | 内容 |
|---|---|
| `[71]` | response_valid；保留到接受下一条非 NOP 命令 |
| `[70]` | transport_busy；请求尚未完成 |
| `[69]` | overrun；忙时提交了额外请求，该额外请求被拒绝 |
| `[68:64]` | 命令错误码；仅在 valid=1 且 busy=0 时解释 |
| `[63:32]` | 对应请求的地址 |
| `[31:0]` | 命令返回数据 |

主机先确认 mailbox 空闲，扫描请求，再反复扫描全零 NOP 直到 valid=1、busy=0。
扫描请求时读出的内容属于上一条命令，必须忽略。接收下一条命令会清除旧响应有效位
和 overrun；TMS TAP reset 只恢复 IR/扫描状态并清 overrun，不取消在途请求。
硬件复位才同时复位两侧邮箱。通信超时后不能盲目重发有副作用的命令。

TCK 和系统时钟使用 request/ack toggle 两拍同步。payload 在 request 发出到响应期间
保持不变；响应 payload 保持到下一次完成。两域复位异步置位、各自同步释放。
系统推理和完成锁存不依赖 TCK 持续运行。

## 命令

除 READ/WRITE/MODULE 命令外，地址必须为 0。除 PARAM_COMMIT 外，控制命令数据也必须为 0。

| opcode | 名称 | 数据 / 行为 |
|---:|---|---|
| `0x01` | READ | 读取下表寄存器或只读存储窗口 |
| `0x02` | WRITE | 写配置、模型、参数 staging 或输入窗口 |
| `0x03` | MODEL_BEGIN | 取消旧模型和输入有效性，清装载计数和参数 staging |
| `0x04` | PARAM_COMMIT | data=0/1/3，对应 Conv1/Conv2/FC |
| `0x05` | MODEL_COMMIT | 482 个权重半字、三组参数全部提交后设 model_valid |
| `0x06` | INPUT_BEGIN | 取消旧输入有效性，开始新的顺序输入传输 |
| `0x07` | INPUT_COMMIT | 收齐 80 个 packed word 后设 input_valid |
| `0x08` | START | 启动完整网络，消费 input_valid，返回本次 job_id |
| `0x09` | ACK | 清 sticky done 和结果快照；忙时拒绝 |
| `0x0a` | CLEAR_ERROR | 清诊断错误；不会解除 watchdog 故障锁定 |
| `0x0b` | SOFT_RESET | 可中止在途计算；清控制状态、计数、结果和加载有效性 |
| `0x20` | MODULE_WRITE | 扩展模块编程端口；当前生产顶层返回 11 |
| `0x21` | MODULE_READ | 扩展模块查询端口；当前生产顶层返回 11 |

复位不清除所有物理 RAM 位，**但会清除其可运行有效性，必须重新装载模型和输入**。
未写入的物理地址值不保证为零，不应把它们当作有效模型或输入。

更新是“执行资格上的事务”，不是双份模型 RAM 的原子交换：MODEL_BEGIN 后旧模型
立即不可执行；更新失败时重新 MODEL_BEGIN 重传即可，不会误执行半更新模型。
普通诊断错误首次锁存直到 CLEAR_ERROR；watchdog TIMEOUT 会覆盖旧诊断以保留故障原因。

## 地址空间

以下地址直接写入命令包，不沿用原 SoC 的 `0x70000000` MMIO 基址。
所有 READ/WRITE/MODULE 地址要求 4-byte 对齐，高位不会被截断后别名访问。

| 字节地址 | 名称 | 读写与说明 |
|---|---|---|
| `0x0000` | VERSION | RO，`0x00010000` |
| `0x0004` | STATUS | RO，见下文 |
| `0x0008` | ERROR_CODE | RO，sticky 诊断错误 |
| `0x000c` | CAPABILITIES | RO，bit0 packed input；1 readback；2 update；3 perf；4 watchdog；5 extension |
| `0x0010` | CLASS_COUNT | 默认 4；1–8；只允许 MODEL_BEGIN 后修改 |
| `0x0014` | TIMEOUT_CYCLES | 默认 100000，非零；无任务/结果/故障时可写 |
| `0x0018/1c` | JOB_ID / COMPLETED_ID | 接受启动的序号 / 最近成功完成的序号 |
| `0x0020/24` | PERF_TOTAL / TASK_ELAPSED | NPU 核心周期 / 控制器启动后的经过周期 |
| `0x0028/2c` | WEIGHT_HALVES / INPUT_WORDS | 0–482 / 0–80 |
| `0x0030` | MODEL_ID | RO，1=当前固定 TinyCNN-8-Flat |
| `0x0034/38` | ERROR_ADDR / PARAM_MASK | 最近锁存错误地址 / 参数已提交位 0、1、3 |
| `0x003c` | IRQ_ENABLE | 默认 1；空闲可写 0/1 |
| `0x0040/44/48/4c` | PERF | 权重行、issue、retire、peak inflight |
| `0x0050` | PERF_FLAGS | bit0 valid；bit1 overflow |
| `0x0060..74` | PERF_LAYER[0..5] | C1、P1、C2、P2、保留、FC；融合池化计入卷积 |
| `0x0100..11c` | RESULT[0..7] | RO，稳定的 INT32 结果快照；可在任何时候读取，以 done 判断有效 |
| `0x1000..113c` | INPUT_PACKED | WO，顺序写 80 个 LE word，低 byte 对应低激活地址 |
| `0x2000..27fc` | WEIGHTS | 空闲 RO；MODEL_BEGIN 后只允许从 0x2000 顺序写 482 个半字 |
| `0x3000..33fc` | ACTIVATIONS | 空闲 RO，1 KiB 实际激活，LE packed word |
| `0x4000+layer*0x100` | PARAMETERS | 空闲读已提交值；MODEL_BEGIN 后写 staging，见下文 |
| `0x5000..501c` | LIVE_LOGITS | 空闲 RO，直接查看 NPU 输出寄存器 |

STATUS 位：0 busy；1 model_valid；2 input_valid；3 done；4 error；
5 model_loading；6 input_loading；7 faulted。busy 包括启动握手和核心复位保护。
未完成的模型/输入收集通过 loading 位表达，仍允许顺序传输及状态查询。

权重布局仍为 Conv1 `[0,9)`、Conv2 `[9,81)`、FC `[81,241)` 个 64-bit word。
每 word 先低半字，再高半字，高半字到达时写入 NPU。`[241,256)` 可查询但不属于
本模型的必需镜像，当前控制器不允许写这部分，以免把超长镜像静默截断。

参数层 0/1/3 有效，2 保留。层内 `+0x00..1c` 为 8 个 bias、`+0x20..3c`
为 8 个 multiplier、`+0x40..5c` 为 8 个 shift。FC 只支持 bias。
每组 staging 不能混用不同层；提交后清 staging/mask，不能重复提交同一层。
卷积提交要求 24 个槽全部写过；FC 要求 8 个 bias（无效类补零）。
multiplier 为非负 INT32；shift 必须以完整有符号 INT32 表达 `[-31,31]`，
例如 -1 写 `0xffffffff`，不是写 `0x0000003f`。读回 shift 为符号扩展 INT32。

只读存储查询在计算/复位期间返回 BUSY，避免读端口与 NPU 争用。
推理后输入前 160 B 已被 Pool2 覆盖；Pool1 位于 320..959，查询看到的是当前物理内容。
logit 输出仍是逐输出通道量化的 raw logits；最终分类必须使用部署包的 `fc_cmp_mult/shift`。

## 错误码与恢复

| 码 | 说明 |
|---:|---|
| 1 | 非法 opcode |
| 2 | 非法/只读/保留地址 |
| 3 | 未对齐地址 |
| 4 | 计算忙或复位保护 |
| 5 | 生命周期不允许或故障锁定 |
| 6 | 传输顺序错误或参数层混用 |
| 7 | 模型、参数或输入未完整提交 |
| 8 | 非法参数、控制命令地址/数据 |
| 9 | 结果尚未 ACK |
| 10 | 任务或扩展事务超时 |
| 11 | 扩展端点未实现 |
| 12 | NPU 未 ready |

普通错误不会悄悄取消正在运行的正确任务。watchdog 超时会终止后端并设 faulted，
保留故障通知；CLEAR_ERROR 仅清诊断，解除 faulted 必须 SOFT_RESET。
软复位后重新装载模型、输入即可再次运行。主机通信 timeout 与硬件 watchdog 是两回事：
前者不能证明命令未执行，客户端不会自动重发 START/COMMIT。

## 模块扩展

`npu_control_subsystem` 参数 `ENABLE_EXTENSION=1` 时启用 ext 端口：
`ext_valid_o/ext_ready_i`、`ext_write_o/ext_addr_o/ext_data_o` 和
`ext_rsp_valid_i/ext_rsp_ready_o/ext_rsp_data_i/ext_rsp_error_i`。
请求内容在 ready 之前保持，响应等待到 valid；单 outstanding；等待请求接受和等待
响应均受 TIMEOUT_CYCLES 限制。`ext_reset_o` 在软复位/超时时通知扩展后端丢弃在途状态。
后端必须遵守 reset，避免上次事务的迟到响应污染下一次运行。

建议未来在 ext_addr 高位划分 module_id，低位保留寄存器/descriptor 偏移；
该地址约定尚未固定。新增可编程拓扑需要相应的后端、descriptor 校验和执行语义，
并更新 MODEL_ID/CAPABILITIES/ABI；目前没有在控制器中假装解释网络图。
若未来需要主机离线后的长序列执行，可在 ext 后端增加 descriptor sequencer，
无需把 JTAG 通道改回 CPU 调试通道。

## 主机使用

接好 fabric JTAG 的四根信号和公共地，先加载对应 bitstream，再启动 OpenOCD：

```sh
openocd -f <实际适配器配置.cfg> -f software/host/nocpu_openocd.cfg
python software/host/nocpu_jtag.py status
python software/host/nocpu_jtag.py infer-vectors --directory build/nocpu_regression/random_4
python software/host/nocpu_jtag.py read 0x2000 --count 8
python software/host/nocpu_jtag.py reset
```

使用已有部署包：

```sh
python software/host/nocpu_jtag.py infer --package data/model.npz --input frame.bin
python software/host/nocpu_jtag.py infer-reuse --input next_frame.bin
```

`frame.bin` 必须是预处理后恰好 320 B 的 signed INT8 特征，不是 PCM 音频。
`infer` 装载模型、推理并使用包中的比较参数返回类别；`infer-reuse` 不重装模型，
只返回 raw logits。`infer-vectors` 支持 synthetic 文件布局和现有真实语音向量布局。
API 成功读完结果后自动 ACK。使用期间由单个主机独占 JTAG mailbox。
本次未进行物理适配器/OpenOCD/板卡联调，这些命令是实现入口，不是板上验收记录。

## 验证与可重复构建

```sh
python hardware/soc/sim/scripts/run_nocpu.py --output build/nocpu_regression
python hardware/npu/scripts/run_tinycnn8_fused_test.py --summary build/fused.json
python hardware/fpga/scripts/build_nocpu.py --vivado-root D:/Project/Vivado/2025.2/Vivado --output build/nocpu_synth
```

`build_nocpu.py` 默认仅综合。加 `--implement` 才进行布局布线和 bitstream 生成；
从不自动烧板。为绕过 Vivado Windows 中文路径的 IP 生成错误，它把所选源码复制到
英文临时目录，完成后把报告、checkpoint、日志及精确源码 SHA256 复制回指定的新
`build/` 目录。也可在英文路径用现有 `build_ego1.ps1 -SynthesisOnly`。

本次实际通过的验证：

- 三组控制器测试：1/4/8 类，random/extreme/impulse，模型更新后换用另一组 random。
  每组 5,918 条命令、3,221 次读回检查。覆盖非零 logits、全部有效权重和参数、输入和
  两级池化激活；覆盖参数混层、乱序/不完整传输、非法命令/地址/shift、NPU not ready、
  重复启动、忙时读写、未 ACK 结果、模型复用、超时、故障清除和中途软复位。
- 每条直接命令故意阻塞响应 1–7 拍，检查 valid/data/error 保持且不能接受/重执行新请求。
- JTAG 顶层：741 条命令、171,647 个 TCK 上升沿；全部模型/输入/结果经过公开引脚；
  两次非零推理、TCK 停止/重启、在途 overrun、TMS reset 和物理 reset。
- 实际 Python `Npu` API 生成 639 条命令，回放到真实 RTL，检查装载、启动和 logits。
- 原融合核回归：7 个单元配置、21 个 golden 用例、33,936 次逐元素比较通过；
  两种旧 MMIO 核选择、4×4/4×8、容量边界均通过，核心仍为 2,805 周期。

这些是 synthetic 非零向量验证，不替代真实训练部署包和板上 JTAG 验收。
CI 已增加无 CPU 回归与主机 API 命令回放。

## FPGA 综合结果

Vivado 2025.2，`fpga_top`，EGo1 `xc7a35tcsg324-1`，50 MHz，**仅综合后**。
证据在 `build/nocpu_synth_20261017_v3/`，含 `provenance.json`、完整资源、时序、DRC、CDC 报告。

| 资源 | 用户提供的 CV32E40P | 用户提供的 PicoRV32 | 本分支无 CPU |
|---|---:|---:|---:|
| 整机 LUT | 14,423 | 11,156 | **7,059（33.94%）** |
| FF | 11,210 | 9,975 | **5,003** |
| DSP | 41 | 36 | **36** |
| BRAM36 | 未提供 | 未提供 | **0** |

相对所提供 PicoRV32 数据，整机 LUT 少 4,097（36.7%），FF 少 4,972（49.8%）。
两组参考数没有在本次用同一工具入口重跑，因此它们是参考比较，不是严格受控的 A/B 实验。

| 本次分层 | LUT | FF | DSP |
|---|---:|---:|---:|
| 任务/装载控制器 | 2,106 | 1,231 | 0 |
| JTAG 传输 | 103 | 319 | 0 |
| 含实际存储读回接口的 NPU | 4,834 | 3,408 | 36 |

分层综合优化可能使子项相加与总数相差几个 LUT。控制器包含参数 staging、
结果快照、周期计数和完备诊断，**本体并未比参考 PicoRV32+桥的 1,998 LUT 更小**。
整机优势主要来自移除 AXI/Debug/共享主存体系。只读查询的端口、mux 和存储推导成本
也已包含在本次数字中，不能拿它与不带读回的 NPU 面积直接作净差。

综合时序估计 WNS +3.455 ns、WHS +0.043 ns；CFGBVS-1、REQP-1839、RBOR-1
专项 DRC 零违例。完整 DRC 仍有 48 项 DSP 流水线建议。板级 I/O delay 仍有缺项，
因此这些时序不能视为完整的接口/布局布线收敛结论。

Vivado CDC 报告识别到两组 ASYNC_REG toggle 同步器和同步释放复位；同时保留
108 项 CDC-15 警告，对应保持到握手完成的 bundled payload，总线没有逐 bit 同步。
未添加 waiver 或屏蔽报告。其正确性依赖上述稳定性握手约束；本次仿真检查了停钟、
在途复位和背压，但尚未完成布线后的 CDC 物理延迟签核。

本次未布局布线、未生成/烧写 bitstream，未测量板上功耗或端到端 JTAG 吞吐。
旧 CPU 固件/SAIF 工作负载仅用于 `my_soc_cpu_reference`，不能直接映射到新的无 CPU
层次来估计功耗；后续功耗测量需要采集本控制器的真实 JTAG/推理活动。
删除 CPU 不改变 NPU 内部 2,805 周期计算；1 MHz JTAG 的协议装载时间可能远大于
56.1 us 核心计算，应分别衡量冷启动模型装载、每帧传输、计算和结果读取。
