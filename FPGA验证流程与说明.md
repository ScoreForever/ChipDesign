# FPGA验证流程与说明

本文用于说明目前项目的FPGA验证方法、实际操作顺序和人工签核要求，便于按同一套标准核对结果。

目前项目采用无CPU的SoC架构，主机通过JTAG装载模型和输入，片上的命令控制器接收START命令后，由NPU自主完成推理，再由主机读回输出进行比较。当前接口为 **ABI 5.0，8×8 PE阵列，最多128条描述符，两颗8 KiB SRAM**。权重为INT4，激活支持i4/u4，原始累加结果为INT32。

## 一、FPGA验证主要检查什么

FPGA验证主要回答三个问题：第一，RTL实现的功能是否与软件参考一致；第二，设计映射到实际器件后，能否在目标时钟下满足时序；第三，经过真实JTAG链路装载和运行时，是否会出现仿真中未暴露的问题。

因此，整个流程按照下面的顺序进行：

**冻结源码 → RTL回归 → Vivado综合与布局布线 → 时序和CDC复核 → 下载镜像 → 实板功能与异常恢复测试 → 人工签核。**

前一阶段存在未解决的问题时，先保留现场、分析原因，再使用新的结果目录重跑。不能仅根据“成功生成bitstream”或“模型跑通一次”，判断本轮验证已经完成。

| 验证阶段 | 主要检查内容 | 需要保留的结果 |
|---|---|---|
| RTL功能回归 | 算子、融合、数值边界、存储访问、错误处理 | summary、编译和仿真日志、参考输出 |
| FPGA实现 | 资源是否超限，目标频率下setup/hold是否满足 | 实现摘要、时序、资源和DRC报告 |
| CDC复核 | 请求和响应跨时钟域时是否完整、稳定，约束是否覆盖实际端点 | 动态仿真日志、布线后CDC报告、端点清单 |
| 实板功能 | 版本能力、模型装载、输出正确性、重复推理与周期 | 下载日志、模型和输入、实际输出、性能计数 |
| 异常恢复 | 停TCK、重复START、超时、协议复位、物理复位和断电恢复 | 测试前后状态、操作记录、恢复后的推理输出 |

FPGA验证通过后，可以作为流片前功能验证的一部分；ASIC侧的DRC、LVS、多工艺角时序及功耗等检查仍由EDA流程分别完成。

### 验证代码之间的关系

项目中的验证代码可以分成四层。第一层生成测试模型和参考答案；第二层驱动RTL或实际板卡；第三层观察输出、状态和中间行为；第四层重新核对证据。阅读代码时，需要先确认它属于哪一层，再判断它的通过结果能够说明什么。

| 代码入口 | 实际验证对象 | 比较方法 |
|---|---|---|
| `run_program_regression.py`、`tb_npu_program.sv` | 真实控制器、执行核、存储和可选JTAG前端的RTL | 命令返回值、错误码、最终输出、物化张量的写回事件及协议断言 |
| `tb_npu_memories.sv`、`tb_npu_shared_stress.sv` 等块级测试台 | 存储、队列、转发、MAC、量化等局部模块 | 已知向量或独立scoreboard，与每次有效操作比较 |
| `run_npu_jtag_cdc.py` 及CDC测试台 | 专用TAP、跨域邮箱，以及给定构建的CDC端点与约束 | 事务次数、数据稳定性、复位恢复、端点集合和物理余量 |
| `build_nocpu.py`、`build_ego1.tcl` | FPGA映射、布局布线和时序约束 | 工具报告与构建门限，不进行模型数值计算 |
| `nocpu_board_check.py`、`nocpu_board_suite.py` | 实际板卡上运行的模型与协议 | JTAG读回结果与golden逐字节比较 |
| `nocpu_board_cdc.py` | 实板异常与恢复 | 操作前后BUSY、错误码、有效状态和恢复输出 |
| `nocpu_e2e_timing.py`、`npu_e2e.py` | 实板部署与推理链路耗时 | 主机分阶段计时、golden输出和硬件性能计数 |
| `nocpu_evidence_check.py` | 已保存的自动板测证据 | 重读文件、重算哈希、核对用例完整性，不重新访问硬件 |

上述脚本均通过现行无CPU路径工作。`CPU_GDB_HISTORY.md` 中的CPU单步、固件、DMA和CPU中断寄存器检查属于历史架构，不是当前签核项目。当前RTL仍有 `irq_o=done || faulted`，表示完成或故障通知；这不意味着设计中存在CPU。RTL测试台可以直接观察IRQ，实际JTAG主机通常查询状态寄存器确认结果。

## 二、验证平台与两路时钟

目前使用EGo1 FPGA板卡，远程Windows工程的主要位置为 `D:/IWS/advanced_soc/ChipDesign`，可通过 `ssh mywin` 连接。Vivado负责综合、布局布线和配置下载；OpenOCD和Python主机工具负责NPU的JTAG测试。实际运行前，需要核对服务器上的工程版本和工具路径。

实际通信链路为：

**Python主机工具 ↔ OpenOCD ↔ FT232H ↔ 专用TAP ↔ 跨时钟域邮箱 ↔ 片上命令控制器 ↔ NPU及共享存储。**

这里有两套不同的JTAG连接。FPGA配置下载线用于烧录bitstream，FT232H连接的是设计中的NPU专用TAP。核对设备时不能把两者的IDCODE、序列号或接线混在一起。

| 时钟 | 当前验证设置 | 作用 |
|---|---|---|
| NPU系统时钟 | 50 MHz，周期20 ns | 驱动命令控制器、NPU和存储访问 |
| JTAG TCK | OpenOCD设置1000 kHz，即1 MHz | 驱动专用TAP的数据扫描与命令传输 |

TCK频率来自实际OpenOCD配置，不等于NPU的工作频率。START被接受后，NPU使用系统时钟独立运行；停止JTAG扫描应当不影响已经启动的计算。

项目中的 `software/host/nocpu_openocd.cfg` 使用 `adapter speed 1000`。如果修改TCK，必须记录新配置；配置频率与仪器测得的频率也要分开填写。

## 三、开始前先固定本轮版本

每轮验证首先记录Git提交号、未提交改动、约束版本、模型包和测试配置。存在未提交的RTL改动时，单独写下提交号还不够，需要一并保留差异和源码哈希。

在项目根目录检查：

```bash
git rev-parse HEAD
git status --short
```

建议每个候选使用独立目录，例如 `build/fpga_signoff/abi5_candidate_20261110_01/`，其中分别保存源码身份、RTL结果、实现报告、下载记录、板测和人工签核表。构建脚本要求输出到 `build/` 下尚不存在的新目录。

复制 [FPGA人工签核表](verification/fpga/SIGNOFF_CHECKLIST.md)，填写本轮候选编号、执行者和复核者。后面的每一项结论都需要能追溯到这份候选的原始结果。

## 四、先完成RTL回归

先检查计算功能，再分别检查行为存储、FPGA存储配置和SRAM宏接口模型。三者对应的实现路径不同，不能用其中一种配置的通过结果代替全部配置。

下面的命令均在项目根目录执行，输出目录仅为示例，重跑时更换编号。

```bash
# 行为存储：程序、JTAG和发布模型回归
python hardware/npu/scripts/run_program_regression.py --packaged-models --jobs 4 --output build/rtl_behavioral_01

# FPGA存储配置：检查实际BRAM响应路径
python hardware/npu/scripts/run_program_regression.py --quick --packaged-models --define CHIPDESIGN_FPGA_MEM --jobs 4 --output build/rtl_fpga_mem_01

# ASIC SRAM宏接口模型：检查宏接口与访问语义
python hardware/npu/scripts/run_program_regression.py --quick --packaged-models --macro-model hardware/npu/tb/ra1shd_functional_model.sv --jobs 4 --output build/rtl_macro_01

# 非对齐通道的激活取数专项
python hardware/npu/scripts/run_gather_regression.py --output build/gather_01
```

这里的 `--quick` 会缩减传输组合，不能将其理解为完整JTAG覆盖。SRAM功能模型也不包含真实工艺延迟，宏模型仿真通过不能代替ASIC时序验证。

### 1. 程序回归怎样生成输入和参考结果

入口为 `hardware/npu/scripts/run_program_regression.py`。`fixtures()` 定义小型网络、张量形状和定向输入；启用 `--packaged-models` 后，再加入七个发布模型的第一个golden输入帧。

每个用例首先调用 `compile_model()`，将模型转换成真正装入硬件的描述符、权重和参数镜像。模型包的manifest同时记录输入/输出位置、类型、尺寸、物理描述符以及需要物化的中间张量。随后，`reference()` 按软件整数语义计算逻辑张量结果，并按i4/u4或INT32格式编码成参考字节。

这里同时使用了编译器和软件参考模型：编译器决定“硬件执行什么、写到哪里”，参考模型提供“数值应该是多少”。两者都属于仓库的软件实现，因此不能宣称所有结果都来自完全独立的第三方golden。

### 2. Recorder怎样把主机调用变成RTL激励

`Recorder` 实现与实际JTAG transport相同的命令接口，但它不连接板卡，而是记录 `Npu.load_model()`、`Npu.infer()` 发出的操作，生成 `commands.txt`。

每条普通记录包含操作码、地址、写数据、预期错误码、比较掩码和预期返回值。例如，读取输出时，记录中的预期值来自软件参考结果；掩码用于只比较有效字节。Recorder返回给Python的值只是帮助生成后续命令，并不是RTL已经计算出的结果。

真正的检查发生在仿真阶段：`tb_npu_program.sv` 读取命令文件，向真实RTL发送请求，再把RTL返回的数据与命令中保存的预期值比较。如果实际输出错误，测试台会触发 `$fatal`，不会因为Recorder预先知道答案而放行。

| 驱动方式 | 测试台怎样发送命令 | 主要覆盖范围 |
|---|---|---|
| native接口 | 驱动命令valid/ready，等待响应，并人为施加响应背压 | 控制器、执行核、存储及请求/响应协议 |
| JTAG接口 | 翻转TCK/TMS/TDI，选择IR、扫描DR，再通过TDO读响应 | 在native计算路径之外，增加串行TAP、邮箱CDC和突发扫描路径 |

因此，native程序通过不能替代JTAG前端验证。命令文件中的 `f0` 是测试台“等待IRQ”的特殊标记，并不是真实JTAG命令；它用于缩短功能测试的完成等待流程，不能用这段等待直接估算实际主机轮询耗时。

### 3. 为什么不仅比较最终输出

`simulate()` 还会依据manifest和软件参考值生成 `writes.hex`。其中记录每次预期写回的地址、半字节写掩码和有效数据，并按照硬件的通道分块与输出顺序排列。

测试台在写回接口实际握手时捕获事件，逐次比较地址、掩码和掩码覆盖的数据位。多写、少写、写错位置、写错有效数据或顺序不符都应失败。这样可以检查生命周期复用之前的中间结果，避免某层写错后恰好被后续覆盖、最终输出又没有暴露问题。

融合后不再落地到存储的逻辑中间值没有对应写回事件，不能据此声称“每个融合内部信号都已检查”。这一部分仍依赖最终数值比较、模块测试和内部断言。

每个模型还会在装载一次后连续推理两次，检查重复运行是否受到旧状态影响，并读取全局性能计数及启用时的trace摘要。数值正确与性能不回退是两种判定，需要分别记录。

### 4. 负向测试怎样证明错误处理有效

`negative_tests()` 先生成合法镜像，再有针对性地修改操作码、保留字段、形状、地址或量化参数，让硬件预验证返回指定错误。例如，非法操作码应返回描述符错误，越界目标地址应返回区域错误。测试不仅检查“有错误”，还检查错误码是否与预期一致。

生命周期测试另外构造未装载START、不完整INPUT_COMMIT、乱序写、BUSY时重复START、结果未ACK时重新装载、运行中RESET和watchdog超时。通过条件是状态转换、拒绝码和恢复行为均符合预期。第128条描述符错误用例还会核对错误位置寄存器，避免只检测到失败却定位到错误的描述符。

### 5. 块级测试与协议检查分别检查什么

| 测试代码 | 激励和比较原理 |
|---|---|
| `tb_npu_mac_int4.sv` | 对MAC阵列施加权重和输入，按测试台参考运算核对结果、valid和标签对齐；同时检查不同阵列配置 |
| `tb_requant_lane.sv` | 读取Python生成的量化向量，比较RTL输出与预期值，覆盖移位、舍入、饱和和钳位边界 |
| `tb_npu_memories.sv` | 对存储接口进行定向读写，检查掩码、未对齐访问、保持响应和刷新等语义 |
| `tb_npu_forward_priority.sv` | 构造环形写队列不同head、占用和地址匹配组合，检查同一地址多次待写时最新数据的转发优先级 |
| `tb_npu_shared_stress.sv` | 并发生成模型读、激活读、掩码写和响应背压，以独立半字节数组保存期望存储内容 |
| `tb_npu_stalls.sv` | 对执行核的模型读、激活读和输出写施加随机等待，检查停顿时数据保持及最终结果 |
| `npu_protocol_checker`，位于 `tb_npu_program.sv` | 在程序仿真期间持续检查控制X态、读请求/响应计数、队列边界、bank访问互斥和模型区保护等不变量 |

共享存储压力测试在读请求被接受时保存参考快照，再在响应被接受时比较，避免误用“响应时刻的最新存储值”作为答案。对于同一拍读写，scoreboard按测试规定的读前写后顺序处理。它也统计请求等待周期，以发现长期饥饿。随机压力的结论只适用于给定负载和种子；任意持续超额负载下的公平性需要单独分析。

协议检查器输出的 `COVER` 计数用于说明预设场景是否被触发，例如队列满、旁路阻塞、窄通道和融合池化。它属于功能覆盖记录，不等于代码覆盖率或形式化证明。

### 6. 极限测试、性能分析和故障注入各自解决什么问题

`profile_bottlenecks.py`、`profile_limits.py` 和 `profile_fuzz.py` 仍然通过程序测试台运行模型，区别在于用例生成方式：瓶颈测试改变K、通道和资源复用等维度；极限测试构造特定形状和容量边界；随机测试按种子生成模型链。它们既比较输出，也读取激活等待、权重等待、结果槽等待等计数，用于判断周期增加发生在哪一类操作上。

这些等待计数不应一律直接相加。不同指标可能重叠，只有代码明确定义为互斥分类的计数才能做时间分解；理论吞吐下界也只是分析参考，不是硬件必然可以达到的周期。

`mutation_test.py` 则反过来检查“测试有没有能力发现错误”：在独立副本中注入队列额度、地址、转发或握手等方面的单点错误，再运行指定测试。如果测试报错，记录为检出；如果仍然通过，则可能是覆盖不足，也可能是行为等价的修改，需要说明原因。

故障注入必须先有同配置、未修改设计的通过基线。还需要查看失败原因，区分真正的输出/协议错误、预期的死锁与无关的编译或工具故障，不能只根据非零退出码就宣称验证能力增强。预先解释的等价修改或只能由静态CDC发现的同步链结构变化，也不能按普通功能错误处理。

人工重点检查以下内容：

- Conv、FC、Pool、ReLU、Requant、AvgPool和Add的结果，融合与非融合路径是否一致。
- INT4/u4/INT32、有符号边界、长K、尾通道、padding、stride和非对齐取数是否正确。
- 描述符上限、最后一条描述符的错误定位、跨SRAM bank访问和模型区保护是否正确。
- 读写背压、写合并、队列转发和层结束时的排空是否正确。
- 模型或输入未装载完成、非法描述符、忙时请求、超时和恢复是否符合预期。

查看 `summary.json` 和原始日志，确认程序运行到最终通过标志。性能优化还需逐模型比较推理周期和等待计数，不能只比较输出。随机用例被编译器拒绝时，应单独记录原因，不能计为已经通过RTL仿真的用例。

## 五、完成Vivado实现并复核时序

在Windows环境下，按实际安装位置执行：

```powershell
python hardware/fpga/scripts/build_nocpu.py `
  --vivado-root D:/AMDDesignTools/2025.2/Vivado `
  --output build/fpga_candidate_01 `
  --jobs 4 --mhz 50 --implement
```

`--implement` 表示继续执行布局布线并生成bitstream。若不加该参数，默认只做综合，不构成完整实现结果。

### 实现脚本的工作原理

`build_nocpu.py` 读取生产filelist，将本次需要的RTL、FPGA顶层、XDC和构建Tcl复制到独立暂存目录。这样可以避免Windows中文路径对IP生成的影响，并固定实际构建输入。脚本记录这些文件的SHA-256，在输出目录保存provenance。

随后，`build_ego1.tcl` 读取源码和约束，生成时钟相关配置，依次执行综合、优化、布局和布线，并导出各阶段报告。时序检查依据的是网表单元延迟、布线延迟与XDC时钟/例外关系，与软件golden数值比较没有直接关系。

脚本会对特定配置和BRAM相关DRC执行额外检查；最终若取不到时序指标，或WNS/WHS为负，会以错误结束。默认实现门限不等于本项目要求的+0.344 ns余量目标，后者还需要单独复核。即使摘要显示PASS，仍要核对外部I/O假设、未约束路径和所有重要警告。

| 报告 | 人工检查重点 |
|---|---|
| `summary.md` | 构建是否完整结束，器件、频率和阵列配置是否正确 |
| `provenance.json` | 实际参与构建的源码、约束及哈希是否对应本轮候选 |
| `route_timing_summary.rpt` | WNS、TNS、保持时间以及未约束路径情况 |
| `route_setup_paths.rpt` / `route_hold_paths.rpt` | 最差路径的起点、终点、逻辑与布线延迟 |
| `route_utilization.rpt` | LUT、FF、DSP和BRAM用量及剩余空间 |
| `route_drc.rpt`、时钟及I/O相关报告 | DRC、时钟关系、外部接口假设和例外是否有依据 |

WNS是最差建立时间余量，负值说明至少有一条路径不满足要求；TNS是负余量的累计值。WHS用于检查保持时间。时序通过至少要确认目标配置下建立时间和保持时间均无违例，并复核约束是否完整。

此前时序恢复任务采用 **WNS≥+0.344 ns、TNS=0、WHS≥0** 作为上板前的时序目标。后续候选若继续沿用该目标，应在本轮签核表中明确填写；不能把“WNS刚好非负”和“达到余量目标”写成同一个结论。

出现负余量时，先定位最差路径，并判断是组合逻辑过长、扇出较大还是布线距离过长，再考虑RTL或布局优化。不要通过放松时钟、删除约束或增加无依据的假路径来掩盖问题。

## 六、重点复核CDC

本项目的主要跨域路径位于TCK时钟域与系统时钟域之间。邮箱采用握手方式传递命令和响应，只允许一条在途事务。

验证时需要同时检查握手控制与捆绑数据。控制信号通过同步链跨域；72位请求和37位响应的数据需要在传输期间保持稳定，并满足对应的物理约束。不能仅根据“有两级同步器”，就判断整个模块安全。

完成本轮实现后执行：

```bash
python verification/fpga/cdc/run_npu_jtag_cdc.py --static-evidence build/fpga_candidate_01 --output build/cdc_review_01
```

该入口运行实际TAP和突发传输的动态验证，并审查提供的布线后CDC结果。输入必须使用本轮实现目录，不能拿旧构建的报告给新RTL签核。

### 动态测试如何检查跨域事务

`tb_npu_jtag_cdc.sv` 直接实例化生产 `npu_jtag_transport`，通过串行IR/DR扫描发送72位命令，而不是只测试一个抽象的两级同步器。

测试台保存发送的完整包，在系统域观察命令valid后比较操作码、地址和数据；人为拉低ready，检查valid和数据是否持续保持；再送入已知响应，经TDO读回后核对响应数据、错误码和地址回显。用系统域接受计数检查一条命令只被执行一次，从而发现丢命令或重复执行。

它还会在邮箱忙时再发送一条命令，确认原请求不被覆盖；在应答到达期间停止TCK，确认邮箱保留应答；在不同事务阶段施加复位，确认不会产生旧请求或旧响应。`tb_npu_jtag_burst_cdc.sv` 另行驱动IR13/14的连续写读路径，检查自动地址推进和突发传输的流水返回关系。

### 静态复核为何要同时看两份报告

`run_npu_jtag_cdc.py` 在动态测试通过后读取两类布线报告，执行的检查不同：

| 输入报告 | 代码实际核对的条件 |
|---|---|
| `route_cdc.rpt` | 解析CDC告警，要求告警类型、严重性和源/目的端点集合与已审查集合一致；出现非预期类型或端点就报错 |
| `route_npu_jtag_cdc.tsv` | 对请求72位、响应37位分别核对源寄存器数、目标引脚数、路径数、20 ns数据路径限制和非负最小余量；另检查同步链第二级的setup/hold路径 |

72位请求对应32位数据、32位地址和8位操作码。37位响应对应32位数据和5位错误码；串行返回包中的其他控制信息或地址回显不应全部算成独立的37位应答跨域数据。

这里保留的CDC-15告警属于已知捆绑数据结构。代码不是删除告警或自动生成豁免，而是检查这些告警是否仍然只对应预期的109个端点，并要求相关物理约束满足。它不验证任意新CDC结构，也不自行重建报告与bitstream的来源关系，因此仍需本轮provenance和人工身份核对。

人工复核内容包括：请求和响应是否对应、忙时是否拒绝覆盖邮箱、数据是否保持稳定、突发读写是否完整、复位后能否恢复，以及约束是否覆盖实际端点。当前检查对象为72位请求和37位响应，共109个捆绑数据端点；原始CDC警告应保留并逐项解释。

还可以运行新增的极限扫描：

```bash
python verification/fpga/cdc/run_cdc_limits.py --jobs 4 --output build/cdc_limits_01
```

这类测试覆盖时钟比例、抖动、同步延迟扰动、异步复位和背压。数字仿真中的延迟注入不等同于模拟真实亚稳态；极限点功能通过，也不代表该频率已经满足布线后的数据稳定时间要求。

**时序和同构建CDC复核通过后，再进行下载和上板。**

## 七、下载镜像并完成实板功能检查

通过Vivado Hardware Manager下载已复核的bitstream，保存下载日志，确认目标器件、DONE和CRC状态。记录bitstream路径与SHA-256，并关联同一构建的provenance。

随后，在已配置好FT232H和OpenOCD的环境中启动：

```bash
openocd -f software/host/lab6_ft232h.cfg -f software/host/nocpu_openocd.cfg
```

使用Windows与WSL组合时，Vivado配置线留给Windows，FT232H按实际USB映射提供给WSL。测试期间只保留本轮使用的OpenOCD实例；与其他同学共用板卡时，先确定占用和释放时间。

先生成冒烟模型，再运行板测。以下PowerShell命令中的模型和结果路径均为本轮新目录：

```powershell
python software/host/make_demo.py --output build/demo_candidate_01

$bit = 'build/fpga_candidate_01/fpga_top.bit'
$hash = (Get-FileHash -Algorithm SHA256 $bit).Hash

python verification/fpga/board/nocpu_board_check.py `
  --bitstream $bit --bitstream-sha256 $hash `
  --provenance build/fpga_candidate_01/provenance.json `
  --package build/demo_candidate_01/package `
  --input build/demo_candidate_01/input.bin `
  --expected build/demo_candidate_01/expected.bin `
  --output build/board_smoke_01 --repeat 3

python verification/fpga/board/nocpu_board_suite.py `
  --bitstream $bit --sha256 $hash `
  --provenance build/fpga_candidate_01/provenance.json `
  --output build/board_suite_01 --repeat 2 --timeout 30
```

执行Python的环境需要能够访问OpenOCD的Tcl端口，默认是本机6666端口；若使用WSL执行，应将命令改为该环境可用的路径和换行方式。

### 冒烟与完整套件怎样检查板卡

`nocpu_board_check.py` 在连接硬件前先核对传入bitstream的SHA-256、provenance中的源码哈希以及输入/golden长度。之后创建 `Npu` 实例读取ABI，检查设备初态，再通过实际JTAG装载模型、重复推理，逐次保存读回字节并与 `expected.bin` 比较，最后检查设备没有残留BUSY、DONE或故障。

它的golden由调用者传入，所以答案是否可信取决于该文件的来源。它也不执行完整算子或异常套件；三次相同输入冒烟通过，不能代替完整回归。

`nocpu_board_suite.py` 使用更多定向网络并分别打开、关闭融合，按实际能力编译模型。每组只加载一次模型，重复运行时交替采用原输入与反向排列输入，并重新计算对应golden。这个设计既检查常驻模型复用，也降低“始终读到上一次结果却误判通过”的可能性。

完整套件的负向测试会发送非法命令，或将合法镜像的某个字段改坏后通过原始装载路径提交，以确认硬件本身拒绝，而不是仅由主机编译器拦截。它核对具体错误码，并在错误恢复后再次推理。若某个必需用例因编译失败而SKIP，总报告不能判为全部通过。

Python `Npu.infer()` 的实际过程是：检查输入长度和打包 → INPUT_BEGIN → 写输入 → INPUT_COMMIT → START → 查询完成或故障 → 读输出 → ACK。该函数不重新装载模型，调用它之前必须有匹配的 `load_model()`。golden输出在主机端比较，不作为模型的一部分发送给板卡。

先读回ABI和硬件能力，确认确实是本轮预期的设计，再检查冒烟、完整功能套件和七个发布模型。发布模型至少逐项记录模型包、输入帧、运行次数、输出匹配情况及推理周期。首帧重复运行只能说明这些输入下的正确性，不能写成完整数据集准确率验证。

出现JTAG超时或连接中断时，原命令可能已经被执行。先检查状态和日志，再决定如何恢复，不能直接重复发送START或COMMIT。

### 部署到完成的端到端计时原理

新入口为 [实板端到端计时脚本](verification/fpga/board/nocpu_e2e_timing.py)，详细参数见 [计时说明](verification/fpga/board/NOCPU_E2E_TIMING.md)。例如：

```bash
python3 verification/fpga/board/nocpu_e2e_timing.py --models all --system-mhz 50 --repeat 3 --output build/board_e2e_01
```

`software/host/npu_e2e.py` 中的 `TimedTransport` 包在真实JTAG transport外面。当现有驱动发出MODEL_BEGIN、MODEL_COMMIT、INPUT_BEGIN、INPUT_COMMIT、START、输出读取和ACK时，记录阶段切换。它不替换计算逻辑，也不增加、删除或重放原有JTAG事务；burst方法仍委托给原transport。

`PhaseTimer` 使用主机单调高精度时钟测量相邻阶段之间的真实经过时间。这样，模型上传阶段包含Python、OpenOCD、USB和JTAG的开销，完成轮询阶段还包含查询往返和驱动休眠。各阶段加总对应一次装载或一次推理的主机耗时。

| 结果字段 | 应怎样理解 |
|---|---|
| `deployment.total_wall_ms` | 能力核对、模型上传及提交返回的总耗时 |
| 每次运行的 `timing.total_wall_ms` | 输入准备查询、输入传输、启动、等待、输出读回和ACK的耗时 |
| `deployment_to_first_output_and_ack_ms` | 从模型装载前开始，到第一次输出读完且ACK返回的连续时间 |
| `compute_ms_from_cycles` | `active_cycles / (system_mhz × 1000)`；是由硬件计数和指定频率换算的计算时间 |
| `resident_median_ms` | 模型只加载一次后，后续重复推理主机耗时的中位数；不含首次运行 |

额外的性能寄存器查询、golden比较、模型编译和结果文件写入放在主要计时区间外，以免将测试记录开销混入推理时间。完成轮询时间不等于纯计算时间，二者存在重叠；不能把硬件计算时间再加到端到端耗时上。

脚本逐次检查golden并保存实际输出，错误后停止且保留失败阶段。它还支持 `--wordwise` 关闭burst、`--legacy-rpc` 切换RPC握手方式，用于比较通信开销。脚本记录的系统频率由操作人指定，TCK读回也只是OpenOCD设置，不是仪器测量。

`software/zoo/e2e_timing.py` 则在RTL中用阶段标记统计仿真时间和TCK数量，适合分析扫描开销；它不包含真实USB/RPC延迟，当前测试台也使用100 MHz系统时钟。两类计时必须注明平台和配置，不能直接混为一组实测结果。

## 八、单独完成异常与恢复验证

```bash
python verification/fpga/board/nocpu_board_cdc.py --output build/board_cdc_01
```

### 异常脚本怎样保证动作发生在BUSY期间

`nocpu_board_cdc.py` 构造较长的Conv/Requant链，制造可观察的运行窗口。它把START、状态读取及相邻测试动作放入同一次OpenOCD RPC中的连续扫描，减少主机往返延迟导致“动作发出时任务其实已经结束”的问题，并核对返回状态中的BUSY。未确认BUSY时应失败，而不能把一次空闲操作算成忙时测试。

停TCK测试是在确认BUSY后不再发送扫描，等待约1秒，再查询完成状态和输出。这里证明的是“测试期间没有请求新的扫描，恢复后结果正确”；若要证明引脚上实际没有时钟边沿，还需仪器波形。

watchdog测试把硬件超时阈值缩短，检查故障码和有效性清理，再协议复位、重新装载并核对恢复推理。协议超时、主机RPC超时和watchdog超时不是同一个事件，日志中需要区分。

该脚本用于专用测试时段，会执行协议复位。需要检查的五项结果为：忙时暂停TCK、忙时重复START、忙时协议RESET、watchdog超时恢复，以及恢复后重新推理。

| 测试 | 需要看到的结果 |
|---|---|
| 确认BUSY后暂停TCK | 恢复扫描后任务已完成，输出正确，计算没有依赖TCK继续跳变 |
| BUSY时再次START | 返回预期的忙错误，不重复启动或破坏原任务 |
| BUSY时协议RESET | 运行状态与有效标志清除，重新装载后可正常推理 |
| watchdog超时 | 报告预期错误，复位和重新装载后恢复 |
| 恢复后重新推理 | 输出与参考一致，无残留错误或旧结果 |

协议RESET清除的是运行状态、模型与输入有效性等控制信息，不表示把两颗SRAM中的存储内容逐字清零。

物理复位按钮和断电恢复必须另做。空闲状态的物理复位可先运行 `nocpu_physical_reset.py arm`，按实体复位键，再对同一结果目录运行 `check`；两次之间不要额外发JTAG命令。该入口不能替代“已采到BUSY后再按键”的忙时复位测试。

断电、上电及重配由操作人记录，再用 `nocpu_recovery_smoke.py` 检查全局复位状态与首次推理。长时间连续运行、实际TCK波形、电气条件及温压边界，也应根据本轮要求单独存证。详细操作见 [上板与证据复核手册](verification/fpga/NOCPU_BOARD_CHECK.md)。

## 九、证据复核与人工签核

完整自动套件结束后执行：

```bash
python verification/fpga/evidence/nocpu_evidence_check.py build/board_suite_01
```

复核器重新检查用例清单、文件哈希及参考与实际输出。退出码1表示证据不完整或不匹配；退出码2表示自动证据核对通过，但板上镜像身份与物理操作仍需人工复核，不能将2直接写成“完整签核通过”。

### 复核器怎样避免只相信报告中的PASS

`nocpu_evidence_check.py` 先按ABI选择必需用例集合，核对融合/非融合条目是否齐全，拒绝重复、缺失、额外条目和无实际运行记录的PASS。之后重新打开每次输入、参考和实际输出文件，比较字节，并重新计算哈希与JSON记录核对。

负向检查还要求用例名、预期错误码和实际错误信息相符。因此，只修改总报告中的 `passed=true` 不足以通过复核。

不过，它没有重新运行板卡或重新计算golden，也没有对完整归档进行不可伪造认证。若参考值和结果同时被错误改写，仅凭这类文件一致性检查不一定能发现。所以需要保留原始生成日志、参考来源和独立人工复核。

文件哈希证明的是文件一致性，不能单独证明板上正在运行该文件。还需要核对下载日志、目标板卡、配置状态和能力读回。参考输出若来自同一套软件模型，也应写明来源；独立golden的覆盖范围另行列出。

每项填写“未执行、待复核、通过、失败、阻塞或不适用”，并记录执行者、复核者和日期。自动脚本生成结果后，由人工核对中间过程与原始文件，再填写 [签核表](verification/fpga/SIGNOFF_CHECKLIST.md) 的最终结论。

## 十、目前已有的验证结果与后续缺项

### 1. 11月9日已经完成的版本

冻结版本为 `81876b3`，详细记录见 [50 MHz时序余量恢复验收](output/npu-timing/20261109/README.md)。

| 项目 | 已有结果 |
|---|---|
| FPGA实现 | 50 MHz，WNS +0.533 ns，TNS=0，WHS +0.028 ns |
| 配置 | ABI 5.0，8×8阵列，生产trace-off配置 |
| 动态与静态CDC | TAP动态回归通过，109个捆绑数据端点已复核，无新增豁免 |
| 七模型板测 | 每个模型取首个golden帧，连续运行两次，共14次，输出正确 |
| 性能核对 | 七模型周期保持，14次运行的24项计数均与同源RTL重放一致 |
| 异常与恢复 | 停TCK、重复START、协议RESET、watchdog恢复和恢复后推理通过 |

这份记录未覆盖该镜像的完整数据集板测、物理复位按键和电气波形检查。其他ABI版本的历史测试不能直接填入本轮签核表作为通过依据。

### 2. 11月10日整合后的RTL

本轮整合改变了模型读取旁路、权重加载流水和MAC寄存器使能，因此必须重新建立源码、实现和板测之间的对应关系。

目前本地 `build/integration_20261110/` 下已有行为存储85项、FPGA存储81项、SRAM宏模型81项程序回归结果，各含19组负向／生命周期检查；另有15组gather专项和150个CDC极限点、0失败的记录。这些属于本地功能仿真证据，后续应随冻结源码归档并复核。

**新RTL还需完成新的Vivado实现、布线后时序与CDC复核，以及对应镜像的实板验证。11月9日的+0.533 ns不能直接作为新RTL的时序结论。**

每轮结束时，按实际范围说明已经验证的功能和尚未执行的项目，确保能够根据相同源码、模型和原始日志复查结论。
