# 双 SRAM 激活存储实施记录

日期：2026-10-26。实现基线为 `73f5580`，保留已合入的性能计数器、trace、主机扫描修复和五算子执行功能。
用户目标为2×2 mm²，作为结构选择与面积估算依据，不要求EDA后严格比较面积是否达标。

## 结构调整

- 模型区8 KiB：描述符、权重、bias及量化参数共用一颗独立32-bit单端口SRAM。
- 激活区8 KiB：输入、中间张量和输出共用另一颗同规格SRAM，代替原4 KiB标准单元张量存储。
- 激活端保留64 B读缓存、字节写合并/转发与8项写队列；缓存及队列属于局部缓冲，不复制完整张量。
- Conv/FC仍共享原阵列；MaxPool、ReLU、Requant及融合路径继续复用原后端，没有新增第二套MAC。
- 算子编码、64-byte描述符和JTAG ABI 2.0命令格式不变。编译器和默认硬件的激活容量改为8192 B，
  模型容量仍为8192 B；主机仍读取能力寄存器检查实际容量。
- 性能计数器和trace布局、事件bit位置不变；写事件改按 `w_valid && w_ready` 接受握手统计，
  背压期间的valid保持不重复计数。`0x40`和性能写计数仍表示逻辑事务，而非写合并后的物理SRAM写命令。

两块物理SRAM可同时执行模型读取与激活访问。单颗激活SRAM自身每拍只能读或写，
读缓存命中、字节写合并和在途写转发用来减少争用。非对齐连续4 B读取需要缓存拼接或两个物理word访问。
该结构没有把单端口SRAM描述成真正的双端口存储。
存储适配器支持任意4位byte mask跨word写、非对齐连续4 B读及尾部未用字节wrap。
同拍接受的读写按read-before-current-write定义，读miss快照期间短暂停止接受新写。

## 顺序与背压

计算核写端增加ready/valid握手和8项输出FIFO，FIFO占用与Requant流水的在途输出一起计入容量预留。
遇到存储背压时，待接受输出的地址、掩码与数据必须保持稳定，不丢弃量化流水返回。

每个输出通道tile及每层结束时强制刷新尚未完整的部分word，并等待激活存储空闲后再继续；
这保证下一层看到全部先前写入。主机INPUT_COMMIT也等待输入写缓冲提交，最终done不会早于输出落到SRAM。
原地ReLU仍需要正确的读后写转发；RESET和watchdog取消读缓存/队列及有效性，SRAM内容不清零。
复位后必须重新提交模型和输入，旧在途操作不得污染新任务。

## 宏映射与构建

默认文件表仍为 `hardware/soc/sim/filelists/chipdesign_nocpu.f`，包括性能/trace模块。

| 存储 | 宏定义 | 默认实例路径 |
|---|---|---|
| 模型 | `NPU_USE_RA1SHD_MODEL` | `i_control/i_model_store/i_sram` |
| 激活 | `NPU_USE_RA1SHD_ACTIVATION` | `i_control/i_activation/i_sram` |

两个实例均为课程 `RA1SHD_2048x32M8`；同一库类型只加载一次。
Genus/DC的 `EDA_NPU_MODEL_SRAM=1` 同时打开两宏，分别由 `EDA_MODEL_SRAM_LIB` / `EDA_MODEL_SRAM_DB` 提供库。
变量中的MODEL沿用现有入口名称，含义为选择两块同类型硬宏，不另保留半套标准单元激活存储配置。
未定义硬宏时使用同步行为RAM，可用于公开仿真和FPGA推断；它不能证明ASIC硬宏映射或面积。

FPGA无CPU入口修复了合并后错接CPU DMI端点约束的构建问题，使用当前 `u_soc/i_host` TAP端点约束与审计。
72位请求、37位响应bundled payload均按min(core period,TCK period)施加datapath-only最大延迟，
保持数据路径可见；只对两个toggle首级D与四组复位同步器CLR施加窄范围异步例外。
同步器第二级setup/hold继续计时，FT232H外部I/O预算不变。详见 [JTAG接口时序](JTAG_IO_TIMING.md)。
旧DMI约束文件仍供原CPU流程使用，不对无CPU网表应用。

ASIC课程PnR模板仍含旧单主存宏的坐标及电源选择，使用新网表时必须按两颗新实例重新规划，
不能沿用旧位置声称完成双宏物理验证。

## 面积估算边界

用户提供的冻结综合图为总3.554493 mm²，其中4 KiB激活标准单元存储2.112219 mm²，
单颗8 KiB SRAM宏0.401444 mm²。保持其余逻辑不变，仅做替换账本：

`3.554493 - 2.112219 + 0.401444 = 1.843718 mm²`

新缓存、队列、握手和仲裁逻辑需另计，工具优化和基线差异也会影响面积。
该数值用于说明替换方向的潜力，不是此次实现已测得的综合面积，也不等于最终die尺寸或布局利用率。
本轮不以2×2 mm²严格达标作为验收门槛。

当前 `73f5580` 基线还含1024×128bit，即16 KiB的trace采样RAM，用户提供的旧Genus冻结面积不含它。
经用户确认，本轮将波形RAM作为可选调试配置：ASIC综合默认关闭，仿真和FPGA默认开启，
性能计数器及逐算子起止、描述符、等待和issue摘要始终保留。这样避免ASIC默认重新出现16 KiB标准单元波形阵列。
新缓存、握手、性能bank和逐算子摘要仍有成本，因此1.843718 mm²账本也不是当前完整设计面积实测。

## 可选波形trace

`npu_trace_capture.WAVE_ENABLE` 的默认值首先采用显式宏 `NPU_ENABLE_WAVE_TRACE=0|1`；
若未指定，则ASIC `SYNTHESIS` 且无 `CHIPDESIGN_FPGA_MEM` 时为0，普通仿真或FPGA为1。
Genus/DC可通过 `EDA_NPU_WAVE_TRACE=0|1` 覆盖；未设置时使用模块的上述默认值。
关闭通过generate从展开后的设计中移除四个1024×32采样数组，保留摘要，不依赖软件运行时关闭来节省面积。

深度寄存器 `0x184` 返回0，`0x180`扩展版本和per-op摘要窗口不变；波形窗口读返回地址错误。
关闭时count、first/last stamp、captured control/offset为0，flags只有job finished可置位。
主机 `trace(include_samples=False)` 继续获取逐算子时间线，`trace()`及波形导出明确提示该硬件未启用波形。
显式开启ASIC波形时，16 KiB存储的宏化/面积/采样带宽需要另行规划，不能沿用默认关闭配置的估算。

## 验证记录的使用

可选波形配置的独立局部检查已通过：普通仿真默认开启、ASIC默认关闭、FPGA默认开启，
以及显式关闭、ASIC显式开启、FPGA显式关闭六种组合。检查覆盖能力寄存器、关闭时窗口拒绝、
逐算子起止/描述符/等待/issue摘要和清除；Icarus展开结果中关闭配置不含波形数组。
正式可复用入口为 `hardware/npu/scripts/run_trace_configuration.py`，测试台为
`hardware/npu/tb/tb_npu_trace_configuration.sv`。本轮局部产物在
`build/dual_sram_trace_configuration_agent/`，`summary.json`记录六配置、展开数组和源码SHA-256。
复现命令：`python hardware/npu/scripts/run_trace_configuration.py --output build/trace_configuration`。
现有14项主机单测通过，另检查深度0时摘要读取成功、波形读取/导出明确拒绝。
Genus/DC的双宏选择、库去重及波形覆盖开关通过Tcl配置mock检查；mock不是技术库综合证据。

必须检查原五算子及合理组合、4×8/4×4、融合/独立、INT8/INT32、上半8 KiB地址、
非对齐读、掩码写、写合并/转发、读写背压、Requant在途量、层间刷新、主机提交、reset/watchdog和JTAG。
真实宏仿真必须同时启用两颗宏，公开测试不提交工艺模型。

2026-10-23/24的功能、Genus、FPGA和板级证据属于各自冻结源码，不能直接记作本轮双SRAM实现的验证结果。
新结果应记录本轮回归路径和源码SHA-256，并区分行为存储RTL验证、真实宏模型、综合、PnR/STA与物理板级推理。

## 本轮完整验证

机器可读证据为 `docs/validation/npu_dual_sram_20261026.json`，对应当前源码哈希。

```powershell
python hardware/npu/scripts/run_program_regression.py --output build/dual_sram_verified --trained-model software/models/tinycnn8_flatten_strong.npz
python hardware/npu/scripts/run_program_regression.py --macro-model build/private_ip/RA1SHD_2048x32M8.v --wave-disabled --output build/dual_sram_macro_verified --trained-model software/models/tinycnn8_flatten_strong.npz
python hardware/npu/scripts/run_trace_configuration.py --output build/trace_configuration
python -m unittest discover -s software/tests
```

- 行为SRAM、启用波形：**70个场景全部通过**，每个连续推理两次。
- 两颗真实课程SRAM宏、关闭波形：**70个场景全部通过**，每个连续推理两次。
- 两套配置各核对 **135268次张量写回事件**；所有场景的周期、逻辑读写计数、性能bank和逐算子摘要逐项一致。
- 保留4×8/4×4、五算子、融合/独立、真实训练模型、JTAG引脚、错误模型拒绝、复位/中止/watchdog覆盖。
  新增8 KiB全容量原地ReLU、上半区INT32输入/量化、随机长写背压及物理提交排空检查。
- 独立20000个Q31黄金向量通过。核心输出FIFO与已预约输出均真实达到8，连续37拍写背压下payload保持稳定。
- 正式SRAM压力TB在行为和真实宏下结果相同：2976次读、15745次接受写，写背压1459拍、读背压18376拍，
  写队列峰值8；覆盖任意mask跨word、读前写快照、响应保持、转发、flush，并最终扫描全部8 KiB。
- 六种波形开关配置及14项主机单测通过；完整ASIC默认配置Icarus展开确认恰有两颗SRAM宏，波形数组不存在。
  该展开检查不是技术库映射综合或物理验证。

缓存跨job保留，因此相同输入的冷/热运行周期可以不同。测试逐run严格核对输出、写回、统计及时间线，
跨run只比较确定性工作量，未为满足旧的周期相等断言而清空缓存。

| 同一4×8网络 | 新版本周期（两次） | 激活逻辑写事务/次 |
|---|---:|---:|
| 小CNN，融合 | 773 / 771 | 41 |
| 小CNN，独立 | 2309 / 2305 | 423 |
| TinyCNN8 | 3854 / 3854 | 808 |
| 训练4类模型 | 3826 / 3826 | 804 |
| 完整8 KiB原地ReLU | 34859 / 34859 | 8192 |

历史4 KiB激活存储版本TinyCNN8为3470周期，新版约增加11.1%。原计算资源、量化契约与输出数值不变，
完整生产握手、输出FIFO和flush产生的周期高于先前简化架构适配器估计；不能把3854周期称为板上时延。

## FPGA结构与时序检查

最终源码在Vivado 2025.2、`xc7a35tcsg324-1`、核心50 MHz/TCK 1 MHz下完成综合及布局布线。
此FPGA配置开启完整波形trace；它的资源不能作为ASIC默认关闭波形配置的面积估计。

| 阶段 | LUT | FF | BRAM tile | DSP | WNS | WHS |
|---|---:|---:|---:|---:|---:|---:|
| 综合 | 16550 | 10684 | 8 | 48 | −0.778 ns | +0.045 ns |
| 布线后 | 16204 | 10684 | 8 | 48 | **+0.056 ns** | **+0.033 ns** |

最终TNS=0、DRC错误0。激活模块在综合中推断为2个RAMB36，另有1225 FF/3014 LUT用于缓存、队列、
快照和控制；没有把8 KiB激活payload展开成触发器。总计8个BRAM tile还包括模型存储和开启的16 KiB波形存储。
ASIC两颗硬宏的选择由独立展开和真实宏仿真验证，本轮未运行ASIC技术库映射综合。

修复了合并后的FPGA构建与旧CPU DMI约束错配：新入口使用 `constrain_npu_jtag_cdc.tcl`，
对当前 `u_soc/i_host` 的72位请求、37位响应payload施加20 ns端点最大延迟并审计实际路径。
仅对toggle第一级D和复位同步器CLR施加窄范围例外；第二级同步链仍检查setup/hold及ASYNC_REG结构。
布线后两组payload余量分别为18.158/18.338 ns，全部109条路径有效；未使用全局clock-group切掉payload。
FT232H短线profile的TDI/TMS/TDO六组外部I/O预算及参考边沿均通过审计。

```powershell
python hardware/fpga/scripts/build_nocpu.py --vivado-root D:/Project/Vivado/2025.2/Vivado --output build/dual_sram_vivado_final --jobs 4
python hardware/fpga/scripts/implement_nocpu.py --vivado-root D:/Project/Vivado/2025.2/Vivado --synthesis-dir build/dual_sram_vivado_final --output build/dual_sram_route
```

输出目录必须尚不存在。实现入口核对源码/检查点哈希，审计CDC/I/O及DRC，时序失败时返回非零；
本次未应用TCK专用时钟脚布线覆盖。+0.056 ns余量较小，后续RTL或工具变化需重跑。
该结果不代表物理USB-JTAG测量、板上推理、ASIC PnR/STA或2×2 mm²芯片已签核；未生成bitstream或烧板。
ASIC物理交接仍需按两颗新宏重新规划floorplan/供电，并建立对应工艺和层次的CDC时序约束。
