# NPU 架构（ABI 2.0，2026-10-26）

## 目标与默认实例树

一个被外部主机调用、可执行不同静态 INT8 网络的推理加速模块，不包含指令 CPU。
主机完成图分析、量化、布局转换、权重打包和生命周期分配；硬件验证能力与边界、顺序执行。

```text
my_soc_top
├─ npu_jtag_transport：72-bit 专用 TAP、CDC、单条在途命令
└─ npu_control_subsystem
   ├─ npu_command_controller：模型/输入事务、完成保护、超时、复位
   ├─ npu_model_store：8 KiB 模型 SRAM，独立同步 32-bit 端口
   ├─ npu_activation_store：8 KiB 激活 SRAM、64 B 读缓存、字节写合并及转发、8项写队列
   ├─ npu_trace_capture：逐算子摘要；可选1024×128bit波形RAM
   └─ npu_program_core：描述符验证、顺序调度、算子执行
      ├─ npu_mac_array：Conv/FC 共享 4×8 MAC、双权重 bank
      └─ requant_lane：单路 Q31 流水，II=1
```

唯一默认源文件表为 `hardware/soc/sim/filelists/chipdesign_nocpu.f`。
阵列可在编译时选 4×4；主机按能力寄存器重新打包，不能把 8 列包直接用于 4 列配置。
无旧 `FUSED_CORE`、固定 class_count、三组参数或旧扩展 endpoint。

## 算子与数值契约

- Conv：batch=1、NHWC、kernel=1×1/3×3、stride H/W=1/2、显式四侧 padding（每侧小于对应 kernel），groups=dilation=1。
- FC：连续输入展平，输入长度≤1024，输出通道≤256；共享矩阵后端。
- MaxPool：2×2、stride2、无 padding、floor 输出尺寸，支持 INT8/INT32。
- ReLU：INT8/INT32，逐元素 max(x,0)。
- Requant：INT8 或 INT32 输入、INT8 输出；逐通道或主机展开后的逐张量 Q31 multiplier/shift/clamp，零点为 0。

Conv/FC 的逻辑输出为 INT32：无偏乘积和采用 26 bit（K≤1024），加 INT32 bias 按两补码 wrap。
4 行阵列局部和为 18 bit；尾部 K 和 Cout 通过 mask 补零/禁止写回。
Requant 契约为 saturating left shift、SRDHM、RoundingDivideByPOT、INT8 clamp；
shift=-31..31，multiplier=0..INT32_MAX。Requant 不额外加 bias，不支持任意非零 zero_point。
不支持的配置明确拒绝；容量约束必须与各维上限同时满足。

## 组合、融合与数据流

主机模型由五种逻辑节点构成，编译器按单消费者条件将 Conv/FC 与后续 Requant、ReLU、MaxPool
中的合法白名单组合降为一条执行描述符。独立执行走激活存储读写，同一处理资源支持流式路径。
可以用 `--no-fuse` 关闭融合；中间 INT32 张量必须真实分配，可能超出容量。

```text
MAC → INT32 bias → 可旁路的窗口 MaxPool → 单路 Requant → ReLU/clamp → 写回
                      ↑                          ↑
              独立 Pool 访存路径          独立 Requant/ReLU 访存路径
```

融合 Pool 采用窗口优先的空间顺序，故只需四个结果向量槽，而非缓存整幅卷积输出。
空间 tile=16，K-major 权重复用。每个最终归约在发射前保留结果槽，覆盖仍在阵列中的返回，
槽可复用时才发射。写回每拍最多一个 INT8 或 INT32 元素；量化在途数据由有效位/计数管理。
读请求与写回均使用 ready/valid 握手。写回背压必须同时约束 Requant 在途量，
避免量化流水的返回覆盖尚未被存储接受的输出；读响应在未接收时保持稳定。

当相同通道的量化为非负倍率的单调变换时，允许把 MaxPool 提前至 Requant/ReLU 之前，
减少 2×2 窗口的量化次数。INT32 bias 的 wrap 加法仍在 max 比较前，不能移动到其后。
连续两个 Requant 不合并；多消费者中间值不省略写回；不支持的链按独立描述符执行。
当前编译器不会融合 Conv→Conv，两层之间的 INT8 张量正常写回。

连续 Conv 的行缓存可行性、权重切换和 spatial-tile 利用代价由
`software/host/explore_conv_streaming.py` 单独探索；它是软件调度验证，不是 RTL 流式模式。
没有为了探索而实例化第二套 MAC 或任意全互连网络。

## 存储和控制边界

默认激活逻辑容量 8192 B，模型区 8192 B，分别由一块独立的单端口 32-bit SRAM 支撑。
描述符、权重、bias/multiplier/shift 共同占用模型区；输入、中间张量和输出共同占用激活区。
最多 16 条物理描述符；执行时不做 tensor-ID 间接查表。
主机输入可选 INT8/INT32，Conv/FC 仍只接受 INT8。主机用张量名做生命周期分析，执行描述符直接引用 byte offset，节省间接查询与元数据存储。

硬件预验证所有描述符与量化参数，验证期间禁止写激活；确认全部合法才置 model_valid。
预验证使用最多17项的有效区域表，检查源是否由声明的模型输入或先前操作覆盖，以及 dtype 是否一致；START 另行要求输入事务已提交。
覆盖旧区域会使旧区域有效性失效；仅 ReLU 的精确同区域操作允许 in-place。
此机制不是对任意模型逻辑意图的形式证明：张量身份、语义依赖、优化正确性仍由主机编译器负责。

每层配置执行期间不变，切换前等待 MAC、结果槽、Requant、模型读和激活写队列全部排空。
激活 SRAM 每拍最多执行一次物理读或掩码写。64 B 读缓存命中时可免去物理读取，
非对齐的连续4 B读取由缓存或两个对齐word拼接；同word的字节写可合并。
尚未提交的字节写必须参与读转发，以保持精确原地 ReLU 和跨算子 RAW 顺序。
层边界及输入提交时强制刷新部分word；全部写入已落到 SRAM 后才发布成功完成。
运行期间锁定模型与输入写入；完成输出保留到 ACK，结果保护通过存储所有权实现，不复制八个 logits。
软复位/超时取消在途操作、读缓存和写队列及模型/输入有效性，不清整片 RAM，重新提交后才能执行。
控制器和存储控制使用同步复位；TAP/CDC 保留独立异步置位与同步释放机制。

## 平台映射

`NPU_USE_RA1SHD_MODEL` 和 `NPU_USE_RA1SHD_ACTIVATION` 分别实例化一颗
`RA1SHD_2048x32M8`，路径为 `i_control/i_model_store/i_sram` 与
`i_control/i_activation/i_sram`（FPGA 顶层再加 `u_soc/`）。同一个库类型只加载一次。
公开默认实现是同步单端口行为存储，可供 FPGA RAM 推断；未定义宏时的 ASIC
综合仍可能把行为存储映射为标准单元，不能据此判断双硬宏方案的面积。

Genus/DC 使用 `EDA_NPU_MODEL_SRAM=1` 同时选择模型与激活两颗硬宏，分别提供
`EDA_MODEL_SRAM_LIB` / `EDA_MODEL_SRAM_DB`；变量名保留现有构建入口，所选库供两颗实例共同使用。
`EDA_NPU_COLS=4|8` 选择阵列列数。旧 tile/权重/psum 环境开关不再使用。
波形trace由 `NPU_ENABLE_WAVE_TRACE=0|1` 显式选择；未指定时，ASIC综合默认关闭，
普通仿真及带 `CHIPDESIGN_FPGA_MEM` 的FPGA构建默认开启。关闭只移除16 KiB波形RAM，
完整性能计数器与逐算子起止/描述符/等待统计仍保留。`0x184`报告波形深度0，
波形数据窗口返回地址错误；主机可用 `trace(include_samples=False)` 读取算子摘要。
Genus/DC通过 `EDA_NPU_WAVE_TRACE=0|1` 可覆盖该默认，显式开启需另行评估调试存储的ASIC映射成本。
芯片目标为2×2 mm²，作为本轮面积估算和结构选择依据，不要求EDA结果严格对标面积门槛。
功能回归、宏仿真、综合和物理验证分别记录；旧2026-10-23/24的周期、面积、FPGA/板级
证据属于当时冻结源码，不能当作双SRAM实现的新验证结果。见
[双SRAM实施记录](NPU_DUAL_SRAM_IMPLEMENTATION_20261026.md)。
