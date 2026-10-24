# NPU 架构（ABI 2.0，2026-10-23）

## 目标与默认实例树

一个被外部主机调用、可执行不同静态 INT8 网络的推理加速模块，不包含指令 CPU。
主机完成图分析、量化、布局转换、权重打包和生命周期分配；硬件验证能力与边界、顺序执行。

```text
my_soc_top
├─ npu_jtag_transport：72-bit 专用 TAP、CDC、单条在途命令
└─ npu_control_subsystem
   ├─ npu_command_controller：模型/输入事务、完成保护、超时、复位
   ├─ npu_model_store：一条同步 32-bit 物理端口，共享模型区
   ├─ npu_activation_store：四个 byte bank，同步 4-byte 读取、掩码写入
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
当前内部写端口固定可接受，随机读延迟由握手处理。

当相同通道的量化为非负倍率的单调变换时，允许把 MaxPool 提前至 Requant/ReLU 之前，
减少 2×2 窗口的量化次数。INT32 bias 的 wrap 加法仍在 max 比较前，不能移动到其后。
连续两个 Requant 不合并；多消费者中间值不省略写回；不支持的链按独立描述符执行。
当前编译器不会融合 Conv→Conv，两层之间的 INT8 张量正常写回。

连续 Conv 的行缓存可行性、权重切换和 spatial-tile 利用代价由
`software/host/explore_conv_streaming.py` 单独探索；它是软件调度验证，不是 RTL 流式模式。
没有为了探索而实例化第二套 MAC 或任意全互连网络。

## 存储和控制边界

默认激活逻辑容量 4096 B，模型区 8192 B。描述符、权重、bias/multiplier/shift 共同占用模型区，
不存在三份分别为 8 KiB 的承诺。最多 16 条物理描述符；执行时不做 tensor-ID 间接查表。
主机输入可选 INT8/INT32，Conv/FC 仍只接受 INT8。主机用张量名做生命周期分析，执行描述符直接引用 byte offset，节省间接查询与元数据存储。

硬件预验证所有描述符与量化参数，验证期间禁止写激活；确认全部合法才置 model_valid。
预验证使用最多17项的有效区域表，检查源是否由声明的模型输入或先前操作覆盖，以及 dtype 是否一致；START 另行要求输入事务已提交。
覆盖旧区域会使旧区域有效性失效；仅 ReLU 的精确同区域操作允许 in-place。
此机制不是对任意模型逻辑意图的形式证明：张量身份、语义依赖、优化正确性仍由主机编译器负责。

每层配置执行期间不变，切换前等待 MAC、结果槽、Requant 和模型读全部排空。
运行期间锁定模型与输入写入；完成输出保留到 ACK，结果保护通过存储所有权实现，不复制八个 logits。
软复位/超时取消在途操作和模型/输入有效性，不清整片 RAM，重新提交后才能执行。
控制器和存储控制使用同步复位；TAP/CDC 保留独立异步置位与同步释放机制。

## 平台映射

`NPU_USE_RA1SHD_MODEL` 实例化 `RA1SHD_2048x32M8`，实例路径为
`i_control/i_model_store/i_sram`（FPGA 顶层再加 `u_soc/`）。
公开默认模型是同步行为存储，可供 FPGA RAM 推断；不能将其当作 ASIC SRAM 映射结果。
激活区未指定专用 ASIC 硬宏，ASIC 工具可能映射为标准单元，需要结合可用小容量宏和端口预算继续评估。

Genus/DC 使用 `EDA_NPU_MODEL_SRAM=1`，分别提供 `EDA_MODEL_SRAM_LIB` / `EDA_MODEL_SRAM_DB`；
`EDA_NPU_COLS=4|8` 选择阵列列数。旧 tile/权重/psum 环境开关不再使用。
最终面积、时序和功耗应以同工艺全系统 PnR/STA 为准；周期比较不能代替物理结果。
