# NPU 冗余审查与可配置推理架构建议

日期：2026-10-22。核对分支 `exp/no_cpu`、提交 `6957d2c`；开始审查时工作区干净。
本文是当前代码审查和后续设计建议，不表示可编程后端已经实现。本次不修改 RTL 或主机 ABI。

## 1. 结论与目标

当前默认 NPU 是“可装载参数的固定 TinyCNN 推理核”。它已经共享计算阵列，并进行了融合、存储复用和位宽收缩；问题主要是多代实现并存，以及网络几何、地址、参数组和层次序散落在控制逻辑中。

建议目标是：**主机将静态模型编译为有界、可验证的层描述符序列；硬件共享一套计算资源，验证描述符后顺序执行；满足条件时选择融合实现。**

这里的自由组合是已支持算子在形状、量化和容量约束内的组合。增加描述符不会自动支持未知算子、任意大小网络、任意布局或动态控制流。具体能力由硬件能力寄存器公布，主机编译器与硬件共同检查。

优先级建议：先统一构建和文档，再分离调度与执行，随后增加独立算子写回和张量存储管理；最后依据综合/布局布线决定面积优化。不要为了通用化同时实例化旧、新两套 MAC 阵列。

## 2. 实际构建与模块边界

### 2.1 默认路径

`hardware/soc/sim/filelists/chipdesign_nocpu.f` 仅列出 8 个源文件，Genus、DC 及 FPGA 默认构建均使用该文件表：

```text
my_soc_top
├─ npu_jtag_transport                         专用 TAP、CDC、单条在途命令
└─ npu_control_subsystem
   ├─ npu_command_controller                 装载事务、保护、结果、超时
   └─ tinycnn8_npu_fused
      ├─ npu_weight_store                    同步权重接口
      ├─ fused_ws_array                      一套 4×8、共 32 个 INT8 MAC
      └─ requant_lane                        一条流水量化通道
```

默认参数为 `WEIGHT_SRAM=1, SPATIAL_TILE=16, ARRAY_ACC_WIDTH=18, PSUM_WIDTH=23`。ASIC 还必须定义 `NPU_USE_RA1SHD_WEIGHT` 并提供匹配宏视图；否则是行为存储，不能当作标准单元工艺的 SRAM 映射结果。

| 代码 | 当前作用 | 是否进入默认硬件 |
|---|---|---|
| `tinycnn8_npu_fused`、`fused_ws_array`、`requant_lane` | 生产计算路径 | 是 |
| `tinycnn8_npu_top`、`conv2d_engine`、`maxpool2x2_engine` | 旧分层 TinyCNN 路径及独立测试 | 否 |
| `fc_engine`、`global_avg_pool_engine`、`global_sum_pool_engine` | 算子参考实现及测试 | 否 |
| `matrix_unit`、`ws_systolic_array`、`ws_pe`、`vector_unit`、`requant_unit` | 旧基础单元与验证 | 否 |
| `my_soc_cpu_reference`、AXI、MMIO wrapper、DMA、CPU | 历史系统和对照 | 否 |

删除未进入实例树的文件不会降低默认网表面积。应先标注生产、参考、独立算子三个构建集合，检查脚本引用后再迁移目录，保留有用测试。旧 `tinycnn8_npu_top` 自身也让 Conv/FC 共享一个 Conv 引擎，不能按文件数量认定多套阵列同时存在。

根 README 同时保留无 CPU 默认路径与旧 `FUSED_CORE=1`、主存路径的叙述；后两者需要明确标为 CPU 参考系统内容，避免团队按不同入口讨论同一个“NPU”。

### 2.2 可配置能力实际上停在哪里

| 位置 | 固定内容 | 对通用化的影响 |
|---|---|---|
| fused 核第 81–123 行 | 权重基址、1 KiB 激活、`PH_C1/PH_C2/PH_FC`、归约组数 3/18/40 | 不是从主机读取的层描述符 |
| fused 核第 297–329 行 | 20×16×1、10×8×8 的坐标与 gather 分支 | 更换 H/W/C 不能只改寄存器 |
| fused 核第 394–406 行 | 输出地址固定按 8 通道计算，量化 clamp 为 `[0,127]` | Conv 强制带 ReLU；不能直接表达无激活 Conv |
| fused 核第 527–545 行 | Conv 只能进入 2×2 Pool 缓冲，FC 只能写 8 个 logits | 缺少独立 Conv 输出和通用张量结果 |
| fused 核第 558–565 行 | 排空后 `phase + 1` | 层数与顺序固定 |
| controller 第 55–83、266–280 行 | 权重 482 个 32-bit 传输、输入 80 个 word、参数组 `0/1/3` | 换核以后控制器仍会拒绝其他模型 |
| subsystem 第 62 行 | `host_parameter_tile` 固定接 0 | 系统只能写一个 8 通道参数 tile |
| `software/host/nocpu_jtag.py` | 241 个 64-bit 权重行、320 B 输入、三组参数、8 个结果槽 | 主机装载格式也绑定网络 |

`MODULE_WRITE/MODULE_READ` 是扩展命令预留。默认 `ENABLE_EXTENSION=0`，顶层扩展端口未连接；这不构成已实现的模型调度能力。

旧 `conv2d_engine` 的确已有形状、步幅、padding、基址等输入，可作为语义与测试参考；但其快速调度仅匹配固定 Conv2，通用路径用组合读存储，验证主要是非零字段检查。不能直接接回生产系统就认为同时获得通用性、同步 SRAM 兼容和当前性能。

## 3. 可以缩减和优化什么

### 3.1 按收益与风险排序

| 项目 | 建议 | 收益及代价 |
|---|---|---|
| 多代顶层、文档和构建入口 | 先标明默认/参考，建立各自文件表 | 维护收益高；当前默认面积收益约为零 |
| 无效兼容参数 | 从新执行核移除 `ACT_BANK_BYTES`、`OPT_GATHER_LOAD`、`OPT_SPATIAL_TILE`，需要兼容时放在适配层 | 避免假配置；参数本身通常无硬件开销 |
| 三组专用 bias/mult/shift 数组 | 改为参数存储 + 当前输出通道 tile 的参数缓存 | 为多层复用服务；需要计算存储与取参开销，不能单纯删除 staging |
| 网络专用控制与地址分支 | 用描述符锁存、嵌套计数器、可选融合模式代替 | 可维护性和通用性收益；增加解码/验证逻辑，不承诺净面积下降 |
| 调试与性能计数 | 增加编译配置，保留总周期、错误、当前 op；详细事件可选 | 减少计数器、饱和判断和读回选择；默认保留便于调试 |
| 大量数据寄存器复位 | 评估只复位 valid/状态，PE 数据在合法装载后使用 | 有望减轻复位网络和复位单元成本；不等于减少寄存器数量 |
| 激活 8 bank 改 4 bank | 实验候选：当前计算每拍最多读连续 4 B，debug 也读 4 B | 容量不变；可能简化选择/译码，需重写跨界寻址并比较映射和时序 |
| 热路径整数乘除与宽计数器 | 用明确位宽和增量地址/嵌套计数器，优先层启动时计算派生量 | 避免通用化引入逐拍动态乘除器；常数运算可能已被综合优化 |
| 控制计数与标签宽度 | 根据声明的最大 K、tile、Cout 和流水在途上界计算位宽 | 有望减少控制 FF；不能用当前三层的上界限制新模型而不公布 CAP |
| 收缩 spatial tile | 作为面积受限配置，不作为默认方案 | 会重复装载更多权重，有明确延迟代价 |

详细 profiler 的保留/移除要按网表检查。未使用的 Pool/GAP 周期 lane 已恒为零，综合器通常会消除，不能把 6×32 bit 接口宽度都算作可删除寄存器。双权重 bank、在途计数和标签用于装载/计算重叠与所有权保护，也不能按“重复”删除。

### 3.2 已经完成的面积缩减不要重复计账

源码及 2026-10-20 的已有报告确认：权重已经移出原 2 KiB FF 风格数组，阵列局部和已从 32 缩到 18 bit，tile 部分和已从 32 缩到 23 bit。

当前主要数组的 RTL 声明量为：

| 数据 | 默认逻辑容量 |
|---|---:|
| 激活 | 1024×8 = 8192 bit |
| `psum_buf` | 16×8×23 = 2944 bit |
| `win_buf` | 4×8×32 = 1024 bit |
| 权重逻辑空间 | 256×64 = 2048 B；固定模型使用 241 行 = 1928 B |
| PE 双 bank 权重 | 4×8×2×8 = 512 bit |
| 固定层参数 | 3×8×32 + 2×8×32 + 2×8×6 = 1376 bit |

以上不含全部流水、控制、接口和结果寄存器，也不等于最终 FF 数。课程宏 `2048×32` 固定为 8192 B，当前 wrapper 的逻辑空间只用其中 2048 B；改小 `WEIGHT_WORDS` 不会把硬宏裁小。希望利用剩余容量，必须同时扩展逻辑寻址、容量检查和主机 ABI。

可考虑把同一宏划分为权重/描述符/参数区，在层启动阶段串行读取描述符及参数，再缓存当前配置；执行期优先供权重读取。这样可能避免额外 FF 数组，但必须预留模型权重容量，并验证端口仲裁。不能把剩余 6 KiB 同时承诺给权重增长、描述符和参数三种用途。

目前不建议将激活也塞进这个单端口宏：每拍激活读取、权重预取、量化结果写回会产生带宽竞争，现有 gather 时序也不兼容同步读，必须建立带宽模型后再决定。

### 3.3 不宜直接删除的“重复数据”

- 参数 staging 是逐个 32-bit 主机写入到整组提交之间的事务边界。可以改成已提交参数存储加写入有效性元数据，不能去掉提交检查后允许执行半个新模型。
- controller 的 `result_logits` 是结果快照，核心也持有 logits。若未来由统一结果存储保留到 ACK，可合并所有权；当前只少一份寄存器可能改变超时、ACK、debug 和复位语义，收益仅 256 bit，应低优先级处理。
- `win_buf` 在加 bias 后保存 INT32 max。不能把它直接变成 23 bit，或先对无 bias 的和取 max 再加 bias：当前 INT32 加法有 wrap，可能改变大小关系。若存储选中的 23-bit 无偏值、比较时重建 INT32，最多省 288 bit，却增加逻辑，优先级低。
- 不要通过清空整片 SRAM 实现“复位干净”。应继续依靠 model/input valid 和在途状态控制可见性。

### 3.4 面积、速度与端到端性能

仓库已有仿真证据，不是本次重新测出的核心性能：默认 SRAM/tile16 为 3028 周期，分段 1063/1573/392；tile8 为 4348，tile4 为 7040。相对 SRAM/tile16，tile8 增加约 43.6%，tile4 增加约 132.5%。这些数字不包含主机数据传输。

50 MHz 时 3028 周期约 60.56 μs。单个 320 B 输入需要 80 个 32-bit 命令，仅每命令一次 72-bit 数据扫描就至少 5760 个 TCK；若 TCK=1 MHz，此项下界为 5.76 ms，尚未计 TAP 状态转换、轮询/响应及 RPC。实际扫描频率和批处理方式需要实测。因此也应优化批量传输和模型常驻，分别报告冷启动、热推理和纯核时间。

当前热推理已复用模型。后续若由主机每算子发命令、搬中间结果，会损害这一优点；应一次提交描述符序列，由硬件内部完成各层。

## 4. 建议的可配置架构

```text
主机：模型导入 → 算子/量化检查 → 布局转换与权重打包
                    → 静态形状推导 → 融合选择 → 张量生命周期/地址规划
                    → 模型包（描述符、张量表、参数、权重）
                                          │
JTAG / 后续总线 → 命令与事务控制 → 已提交的描述符/参数/权重存储
                                          │
                               描述符验证器 + 顺序调度器
                                          │
                              锁存当前 op / 参数 / 地址增量
                                          │
        激活 scratchpad ↔ 通用地址生成 ↔ 一套 4×8 MAC / 部分和
                ↑                              │
                └── 写回/可选 MaxPool ← bias、requant、clamp
                └── 独立 Pool / 后续 Add 共用访存与必要的轻量算术
```

保留 `npu_jtag_transport` 作为传输层；保留 controller 的装载/提交、忙时保护、超时与结果生命周期，但将固定模型规则移到独立后端或新版本描述符验证器。新后端不能继续用固定类数来定义输出。

建议逐步拆为 `npu_model_store`、`npu_descriptor_validator`、`npu_scheduler`、`npu_tensor_addr_gen`、`npu_mac_backend`、`npu_postprocess`、`npu_activation_store`。这是职责划分，初期可将相邻职责放在一个文件，避免为模块数量而拆分。

### 4.1 计算资源的复用方式

- Conv 与 FC/GEMM 共用一套阵列。FC 是矩阵归约模式，具有自己的输入/输出形状和布局定义，不再意味着整个网络结束。
- ReLU/Clamp 通常作为输出属性融合；需要单独执行时走轻量张量遍历，不增加专用乘法阵列。
- Flatten/Reshape 在连续布局且逻辑元素顺序一致时仅修改视图；NCHW Flatten 与 NHWC Flatten 不等价，必须相应变换后续 FC 权重或显式重排。
- MaxPool 可独立遍历激活，也可融合在 Conv 写回前。独立量化后池化可用 INT8 比较；融合路径保留当前 INT32 比较语义。
- 新增 Add 时可共用轻量后处理算术，但须解决双输入读带宽与量化对齐。Depthwise 的阵列映射利用率需要单独评估，不能仅修改 opcode。

通用 gather 不能假定每个四元素 K 分组都对应连续四个有效字节。通道尾部、卷积核跨行、padding 和不同 stride 可能破坏这一条件。初期可用多拍 gather/小缓存保证正确性，再对连续访问走快路径；描述符可配置不等于任意形状都能维持当前每拍 4 B 的供数效率。

当前 Conv1 每组只有三行有效，第四行补零，局部阵列行利用率为 75%；FC 类数小于 8 时也有输出列闲置。这是真实利用率问题，但将阵列列数缩到 4 会增加输出通道 tile，且当前系统端口/参数提交固定按 8 列定义，不能只改核心参数就宣称整个系统支持新配置。应将阵列尺寸作为平台配置，比较全模型周期和实际资源，而不是仅比较 MAC 个数。

## 5. 描述符和模型包草案

先形成 ABI 规范与 Python 参考验证器，再锁定 RTL 编码。下面是字段设计建议，不是现有寄存器协议。

### 5.1 三类对象

| 对象 | 必需内容 |
|---|---|
| 模型头 | magic、ABI 版本、op/tensor 数量、各区起始/长度、入口输入与结果 tensor ID、量化契约 ID、能力需求 |
| 张量表 | tensor ID、dtype、layout、shape、物理基址、分配长度、量化引用、输入/输出/中间张量属性 |
| 层描述符 | opcode、输入 tensor ID（至少预留 src0/src1）、输出 tensor ID、权重/参数引用、kernel/stride/pads、融合与输出属性 |

调度器按 `pc=0..op_count-1` 顺序执行。第一版不需要分支指令、动态依赖搜索或可编程微处理器。静态有向无环图可以由主机拓扑排序，后续 Add 引用前面仍存活的两个张量即可表达残差。

建议区间基址/长度统一用 byte，禁止当前“权重行地址、激活字节地址、参数层号”混在同一个未标记字段。描述符存入固定格式二进制，主机 JSON 仅作为生成输入和可读 manifest；硬件无需解析 JSON/ONNX。

### 5.2 一个可讨论的 64 B 层记录

| word | 内容 |
|---|---|
| 0 | opcode、flags（含输出类型/可选融合）、保留位 |
| 1 | src0 ID、src1 ID；无第二输入用规定哨兵值 |
| 2 | dst ID、参数记录类型/保留位 |
| 3–4 | 权重 byte offset、byte length |
| 5–6 | 参数 byte offset、byte length |
| 7 | kernel H/W、stride H/W，各 8 bit |
| 8 | pad top/bottom/left/right，各 8 bit |
| 9 | groups、dilation H/W；首版只接受 groups=1、dilation=1 |
| 10 | clamp min/max、pool 模式及保留位 |
| 11–15 | 必须为零，留给后续扩展，拒绝未知有效标志 |

形状和布局引用 tensor 表，不在每层重复。Conv/FC 的 Q31 multiplier、shift、INT32 bias 存参数区；独立操作的参数由对应记录类型定义。字段 ID 位宽、大小端、符号扩展、无效 ID、版本兼容规则必须在真正实现前逐字节固定。

若按 32 层×64 B，就是 2048 B 描述符；再按 16 个张量×32 B，是 512 B 张量元数据。该张量条目大小只适合首版受限维度/紧凑布局，需要另行固定字段；支持任意 stride 的版本可能更大。这些是容量预算示例，不是建议立即增加同等大小 FF。

### 5.3 首版能力边界

建议第一阶段提供能完成不同静态 CNN 链的最小集合：

- batch=1；激活为紧凑 NHWC INT8；矩阵/最终结果提供明确的连续 INT32 格式。
- Conv2D：1×1/3×3，stride=1/2，显式 padding，groups=1、dilation=1；H/W/Cout 由描述符配置并受 CAP 限制。
- FC：INT8 输入，INT32 或 requant 后 INT8 输出；隐藏 FC 可继续接后续算子。
- MaxPool：先实现无 padding 的 2×2、stride2、floor 输出尺寸，独立与受限融合两种模式。
- Requant/Clamp 与满足连续性和元素顺序条件的 Reshape。
- 首版 zero_point=0，按输出通道 multiplier/shift，正或零 multiplier；所有不支持的量化配置明确拒绝。

这里的算子集合是待实现目标。第二阶段再加入同形状 Add、AvgPool/GAP、Concat/Copy；Depthwise、广播、任意布局转换、动态形状随后按模型需求决定。不要为了“自由组合”首先实现硬件图搜索。

CAP 至少公布：支持 opcode/模式、各区 byte 容量、最大层数/张量数、最大 H/W/C/K、布局/量化模式、tile、累加宽度。最大尺寸不是各维可独立取最大；还必须同时满足存储与归约约束。

## 6. 硬件必须验证什么

主机负责完整模型语义与优化，硬件负责自身明确支持的操作和内存访问安全。建议分为提交期检查和执行期不变量。

### 6.1 提交期

1. **格式与能力**：版本、区长度、opcode、flags、dtype/layout、非零维度、保留位。拒绝不支持项，不能只表现成 start_ready 永久为零。
2. **形状**：重新验证 Conv 的 `OH=floor((IH+PT+PB-dilationH*(KH-1)-1)/SH)+1` 及 OW；首版 dilation=1。拒绝非正结果和负分子，避免无符号回绕。FC 验证展平长度；Pool 使用声明的 floor 规则；Reshape 元素数一致。
3. **容量与地址**：用加宽的中间计算检查 `base + span <= region_size`，同时检查对齐与区域归属。紧凑张量用元素数×元素字节数；未来带 stride 时按最大可达地址检查，而非只算元素数。地址截位前验证，不允许截断后“落回合法范围”。
4. **权重与参数**：按打包规则验证 K、输出 tile 数及所需 byte 数；每个 Cout 都有 bias/mult/shift；尾部 lane/K mask 正确，不能把旧模型残留作为补齐值。
5. **数值范围**：K 与累加宽度相容；量化 shift/clamp/zero_point 合法；输入/输出量化关系符合对应 opcode 契约。
6. **依赖与内存复用**：每个输入已由入口或先前 op 生成，shape/layout/量化相容，结果尚未被其他输出覆盖。首版禁止一般的 in-place Conv/Pool；只对白名单视图/融合实现开放别名。
7. **提交完整性**：描述符、参数、权重、入口输入全部完成规定装载流程。顺序传输可用 expected offset/count，随机写则需要更细有效性元数据；CRC 可检查传输错误，但不能代替地址与依赖检查。

为避免依赖检查变成复杂硬件图分析，首版限制每个逻辑中间 tensor 单次定义，以顺序表维护 tensor ready 状态。验证器可逐层/逐 tensor 扫描，检查输出物理区间是否与本次及未来仍需要的输入区间相交；在有界层数下用多周期比较换面积。主机提供生命周期用于优化，硬件不能未经验证就把它当作真实生命周期。

只检查本层 src/dst 不相交不够：某个旧 tensor 可能被未来残差 Add 使用。也不能仅保存 tensor ready bit，因为物理地址被复用后 ready 元数据可能已经失效。

提交成功后锁定对应 generation 的描述符/参数/权重。运行期间禁止修改，防止“验证了一个描述符，执行时已经变成另一个”。第一版可以沿用当前 MODEL_BEGIN 立即使旧模型失效的策略；并不需要双份模型 RAM 才能保持不执行半个模型。

### 6.2 执行期

- 层内当前配置锁存；有效输出才能写存储，尾部 lane 不写；实际产生的地址仍受区域边界约束。
- SRAM response 保持到消费，PE 双 bank 只在旧 issue 全部退休后覆盖。
- `ISSUE → DRAIN → COMPLETE → FETCH`：MAC、requant、SRAM、写回都排空后才能换层和参数缓存。
- 看门狗、错误 op index/地址、job ID、完成状态可读；错误后不把部分输出当作完成结果。
- START、ACK、RESET、返回背压和 TCK 停顿沿用单次执行语义。

## 7. 三个最容易阻止通用化的约束

### 7.1 激活存储和张量生命周期

现在布局是 Input `[0,320)`、Pool1 `[320,960)`、Pool2 `[0,160)`；Pool2 覆盖原输入是固定生命周期的结果。

若把 Conv1 与 Pool1 拆开，Conv1 输出 `20×16×8=2560 B`，加 320 B 输入，普通非原地实现同时需要 2880 B，已经超过当前 1024 B。Conv2 不融合时，输入 640 B 加输出 640 B，也需要 1280 B。

因此必须同时做到：激活容量由可验证的模型内存计划决定；保留可选融合减少临时张量；容量不足则明确拒绝，或在后续版本加入有带宽预算的分块/外存路径。两块 ping-pong buffer 足够一些链式网络，但不能覆盖所有残差和多分支的存活集合。

不要默认恢复旧双 4 KiB 激活 bank：先从目标模型的存活峰值、物理 RAM 粒度和带宽选择容量。描述符控制解决表达能力，scratchpad/外存接口决定可执行规模。

### 7.2 累加位宽

保持 4 行阵列、INT8×INT8 时，四个乘积之和范围 `[-65024,65536]`，18-bit 局部和仍然足够，与网络层数无关。

23-bit 全归约部分和则绑定 K：保守使用 `abs(product)<=16384`，需要满足 `K×16384 <= 2^22-1`，即 K≤255；当前最大 K=160。对于 K=256，正极值已需要 24 bit。若允许更大层，不能沿用“23 bit 总是精确”的假设。

可选路线：首版限制 K；或用 `1+ceil(log2(Kmax×16384+1))` 确定编译期宽度并保留 INT32 bias；或支持多个累加配置。PSUM 从 23 扩到 32，在 tile16/8列时增加 1152 bit 声明存储，另有加法和选择开销，需综合比较。

本仓库 golden 采用 INT32 wrap 语义。新后端必须明确是兼容 wrap、约束无溢出，还是另设饱和模式；不能在重构时默默改变。同理，动态支持非零输入 zero_point 若通过 bias 修正，修正项也须验证 INT32 范围；若在 PE 前直接减零点，可能需要超过 8-bit 的激活输入，18-bit 局部和结论也须重算。

### 7.3 量化与可选融合

当前 `requant_lane` 支持 Q31 乘法、移位和 clamp，但没有输入 zero_point 修正或输出 offset；调用方又固定 clamp `[0,127]`。因此“有 INT8 Conv”不等于能直接执行所有导出的 INT8 模型。

LiteRT 规范允许非零激活 zero_point，并对 Conv 使用输出通道相关的量化参数；其标准权重取值范围也与本仓库允许 `-128` 的自定义边界不同。首版可以明确只支持现有对称量化子集，由主机重导出/校准；若要直接接通用模型，需补齐契约并做精度验证。[LiteRT 官方量化规范](https://developers.google.com/edge/litert/conversion/tensorflow/quantization/quantization_spec)

对于同一通道、相同非负 multiplier/shift/clamp 的单调点运算，Conv 的 Requant/Clamp 可以与空间 MaxPool 交换，因此可保留当前融合。若卷积输出有额外消费者、量化参数随池化位置变化，或引入非单调算子，不能自动套用。bias 的 INT32 wrap 加法仍应在 max 比较之前，不能借融合推导改变其位置。

后续 Add 第一版建议只支持相同 shape、相同量化尺度/零点；其他情况先由主机插入显式重定标。普通 ONNX Add 还允许广播，但这属于额外能力，硬件子集应拒绝未实现的广播。[ONNX Add](https://onnx.ai/onnx/operators/onnx__Add.html)

## 8. 顺序组合示例

以下是编译后逻辑序列，不是已实现程序：

```text
模型 A（当前网络）
Conv3x3+Clamp+Pool2x2 T0→T1
Conv3x3+Clamp+Pool2x2 T1→T2
View(NHWC flatten)  T2→T3
FC(INT32)           T3→T4

模型 B（首版支持目标；容量足够时）
Conv3x3+Clamp       T0→T1
Conv1x1+Clamp       T1→T2
MaxPool2x2          T2→T3
View                T3→T4
FC(INT8)            T4→T5
FC(INT32)           T5→T6

模型 C（第二阶段 Add 支持目标）
Conv1x1             X→A
Conv3x3             A→B
Add                 B,X→C
Clamp               C→D
```

模型 C 要求 B 与 X 同形状且量化相容，X 必须保留到 Add 完成。每个例子都能线性执行，无需硬件并行图调度；真正的差别在地址、参数和存储生命周期。

## 9. 建议实施顺序与验收

| 阶段 | 交付 | 验收重点 |
|---|---|---|
| 0：整理与基线 | 默认/参考文件表、统一文档、当前实例树和基准 | 当前接口、Pool1/Pool2/logits、重复推理不变 |
| 1：描述符契约 | ABI、CAP、Python 模型包/验证器、独立整数参考执行器 | 多种拓扑与非法模型都能给出确定结果/错误 |
| 2：参数化执行 | 从 fused 核抽出层内执行，支持独立 Conv/FC/Pool，通用地址/尾部 mask/写回 | 不同 H/W/C、stride/pad、K/Cout 非整倍数；同步存储与背压 |
| 3：硬件顺序调度 | 描述符存储、验证器、pc、模型事务和通用结果 tensor | 同一 bitstream 切换 A/B 模型，不重综合、不让主机搬中间结果 |
| 4：融合与 PPA | 恢复可选 Conv+Pool 快路径，按平台调 bank/缓存/tile | 融合开关逐位一致；完整 SoC 综合、PnR、时序/功耗与端到端延迟 |
| 5：扩展网络 | Add、多活跃张量及按需其他算子 | 残差、生命周期复用、不同量化参数与明确拒绝不支持项 |

不要把“只能接受三条固定 shape 描述符”的阶段当作通用化完成；它只是调度接口迁移。首个有效里程碑应至少运行两种层数/顺序/形状不同的模型。

回归需要覆盖：逐层张量与独立参考一致、融合/非融合一致、不同模型连续运行、模型复用/替换、尾部 lane/K、零/极值 multiplier、shift、INT32 边界、非法 opcode/shape/地址/生命周期、忙时篡改描述符、超时、SRAM response 背压及中途复位。

每项优化单独记录四类证据：RTL 功能/周期，FPGA LUT/FF/DSP/BRAM，工艺匹配的 ASIC 单元/宏面积与 PnR/STA，以及包含装载/传输的板上端到端测试。不能用省掉的 bit 数预测最终 die 面积，也不能把旧配置 PPA 用于新后端。

## 10. 本次核查与复现

源码基线、8 文件生产列表、实际顶层/子模块、主机装载代码，以及已有面积实验 JSON 均已核对。已有核心周期表引用 `docs/validation/nocpu_area_20261020.json` 与 `docs/NOCPU_AREA_OPTIMIZATION_20261020.md`，本次不重跑其完整配置矩阵，不运行综合/布局布线。

本次现有控制/JTAG/主机回归入口：

```powershell
python hardware/soc/sim/scripts/run_nocpu.py --output build/npu_review_20261022
```

本次结果：**ALL CPU-LESS TESTS PASSED**，共 5 项运行：

- controller：classes=1/4/8 均通过；各 5923 条命令、3223 次读回检查。
- JTAG：741 条命令、171647 个 TCK 边沿、2 个有效任务，通过。
- Python 主机轨迹：639 条命令，通过。

日志及 `summary.json` 位于 `build/npu_review_20261022/`（构建产物，不提交）。首次沙箱内启动 Icarus 子进程遇到 Windows `WinError 623`（DLL relocation）；随后以正常本机执行权限重试同一入口并通过。该环境错误不作为 RTL 功能失败。

### 关键源码定位

- `hardware/soc/rtl/my_soc_top.sv`、`hardware/soc/rtl/control/npu_control_subsystem.sv`
- `hardware/soc/sim/filelists/chipdesign_nocpu.f`
- `hardware/npu/rtl/tinycnn8_npu_fused.sv`
- `hardware/npu/rtl/fused_ws_array.sv`、`requant_lane.sv`、`npu_weight_store.sv`
- `hardware/soc/rtl/control/npu_command_controller.sv`、`npu_jtag_transport.sv`
- `hardware/npu/rtl/conv2d_engine.sv`、`conv_window_addr_gen.sv`（旧通用路径参考）
- `software/host/nocpu_jtag.py`、`hardware/npu/tools/tinycnn8_golden.py`

### 外部语义参考

- [LiteRT INT8 量化规范](https://developers.google.com/edge/litert/conversion/tensorflow/quantization/quantization_spec)：对照量化子集，不表示本核完整兼容。
- [ONNX Conv](https://onnx.ai/onnx/operators/onnx__Conv.html)：形状、padding、stride、group/dilation 的模型语义参考。
- [ONNX Add](https://onnx.ai/onnx/operators/onnx__Add.html)：广播属于模型格式能力，需与本硬件子集区分。
