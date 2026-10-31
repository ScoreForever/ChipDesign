# INT4 SoC 重构与交接（2026-10-31）

本轮将默认无 CPU SoC 从 INT8 改为原生 packed INT4，保留主机调用、模型描述符、五算子任意合法组合、融合、背压、复位和超时处理。
分支为 `codex/int4-soc-refactor-20261031`，INT8 父提交为 `88cae88`。
前一项 FPGA 时序修复已单独提交到 `codex/npu-fpga-timing-fix-20261031`；其中时序结果属于 INT8 源码，不能移用到本版。

用户最后明确要求本轮只完成 SoC 重构，ASIC EDA 交由另一位同事执行。
没有上传源码或启动远程综合/PnR；先前启动的两个本地 Vivado 流程也已停止。
下述结果是 RTL、课程宏模型和主机功能验证，不是本版的映射面积、STA、PnR、bitstream 或板级结果。

## 数值与接口

| 项目 | 原 INT8 | 当前 INT4 |
|---|---|---|
| 激活、权重 | signed8 | signed4，-8..7 |
| 物理打包 | 1元素/byte | 2元素/byte，先低 nibble，整张量连续 |
| 原始输出、bias | INT32 | INT32，保留 wrap 加 bias 的语义 |
| 单个乘积 | signed16 | signed8 |
| 四行 MAC 局部和 | 18bit | 10bit，最大256 |
| K组累积 | 26bit | 18bit，K≤1024时范围-57344..65536 |
| Requant | Q31，32×32乘法，64bit乘积 | Q15，32×16乘法，48bit乘积，INT4 clamp |
| 通道参数 | 12B | 8B：bias32 + multiplier16/shift6/保留位10 |
| 主机/描述符地址 | byte | byte，4B基址对齐 |
| 核心激活端口 | byte地址/4bit byte掩码 | nibble地址/8bit nibble掩码 |
| 模型协议 | ABI2.1 | ABI3.0，0x00030000 |

Requant 的非负 multiplier 为0..32767，shift=-31..31；保留位、负系数及-32编码均被硬件拒绝。
四级、II=1 的量化流水仍受写回信用约束。饱和左移使用32bit移位和溢出阈值比较，省去原64bit移位路径。
完整定义见 [模型 ABI](CPU_LESS_CONTROL.md)。

描述符 word15 的 `0x49340f40 | COLS` 绑定数值格式和阵列列数，硬件拒绝旧描述符或列数不符的权重布局。
新增只读能力寄存器0x6c=0x0f200404，主机在 MODEL_BEGIN 前核对 ABI、数值格式、容量和列数。
不保留 INT8 兼容执行路径。

## 存储与数据通路

默认仍使用模型8KiB、激活8KiB两块独立32bit单端口 SRAM。激活8KiB可放16384个 INT4 元素，
INT32 张量仍占4B/元素。宏类型、物理容量、实例名、PAD 和 JTAG 引脚保持原有交接约定。
**宏面积不因打包减半**；逻辑缩减来自 MAC、psum、量化、参数缓存及权重装载控制。

激活存储保留64B读缓存和8项写队列，将合并/转发细化到 nibble。
课程宏只有 byte 写使能，孤立 nibble 更新会执行读旧word→合并→写回；期间锁定物理端口和前端握手，
避免覆盖同 byte 的另一元素。能合并成完整 byte 的数据直接写。层结束、输入提交及最终 done 前继续等待写队列排空。
缓存、队列、响应快照、同拍 read-before-write、跨word和容量末尾访问均纳入检查。

奇数元素张量最后一个 byte 的高 nibble 清零；不会在每行或每通道补齐。
ReLU 精确原地执行保留，其他源/目标重叠继续拒绝。
模型权重一行装载即可覆盖8个 lane；4列或8列窄尾行只占2B，通过地址bit1选择半字返回。
删除原8列权重分两次读取的 load_half/load_low 路径。

Conv/FC→Requant/ReLU/MaxPool 的合法单消费者融合仍通过局部结果槽直接传递。
没有新增第二套 MAC、通用交叉开关或整层寄存器缓存。Conv→Conv仍经打包激活 SRAM 连接。
五算子与形状上限、布局限制见 [架构](ARCHITECTURE.md)。

## 主机与模型迁移

JSON 原生输入为 `i4`（默认）或 `i32`；权重必须在-8..7范围内，越界报错，禁止隐式截位。
`pack_i4/unpack_i4`、`encode_tensor/decode_tensor` 和 `Npu.infer_values` 提供打包与类型检查。
manifest 新增数值格式、打包顺序、逻辑输入/输出元素数；旧包须重新编译。
trace 升为v3/0x00030080，明确模型 byte 地址、激活/write nibble 地址，写回字段表示写入 nibble 数。
ASIC默认不综合波形缓冲和逐算子摘要，全局性能计数器保留；仿真/FPGA默认启用调试配置。

原 TinyCNN NPZ 和训练工具保留为 INT8 训练资料。NPZ 导入现在明确经过有损 `/16` PTQ：
换算权重、bias、逐层单位和 Q15 系数，input_scale乘16，删除旧 fc_cmp_*，标记 `accuracy_validated=false`。
回归证明转换后的包与新的整数参考一致，**不证明分类精度**。部署需重新校准、测量数据集精度，必要时 INT4 QAT。
原 CPU/MMIO runtime 和历史 INT8 golden 工具不在默认文件表内，不能作为当前硬件驱动。

## 功能验证

冻结生产 RTL 后完成两套完整回归，并核对最终文件 SHA-256；详细记录见
[验证数据](validation/npu_int4_20261031.json)。构建目录不纳入 Git，私有宏模型不提交。

| 验证 | 结果 |
|---|---|
| 行为 SRAM，4×8/4×4、融合/独立、原生/JTAG接口 | 82项全部通过；233732次逻辑写回逐项核对 |
| 课程 SRAM 宏模型，关闭波形缓冲 | 同样82项通过；233732次逻辑写回逐项核对 |
| INT4 MAC | 256种输入/权重组合×2权重bank，每个列宽配置512个输出及tag核对 |
| Q15 | 独立参考20000组向量，通过；覆盖INT32极值、饱和、舍入、shift和clamp |
| 存储 | 独立nibble参考、500组随机访问、8KiB全容量扫描、700次孤立nibble RMW、保持响应与复位取消通过 |
| 高压背压 | 8436读/4465写，写阻塞16201拍、读阻塞11569拍，队列峰值8；无丢失/重复 |
| 核心随机延迟/长写回阻塞 | CNN融合/独立、结果槽压力、上半区INT32输入用例通过 |
| 非法模型及生命周期 | 旧格式/列数/量化参数等10类拒绝，另含abort、timeout、不完整传输等生命周期检查 |
| trace构建开关 | 8种组合均通过；ASIC关闭时确认波形数组不存在 |
| 主机单元检查 | 21项通过 |
| FPGA验证工具单元检查 | 18项通过，1项缺少对应本地历史证据而跳过；未连接板卡 |
| EDA交接工具检查 | 5项几何/交接、13项报告解析检查通过；未调用EDA |
| SoC顶层展开 | 默认行为存储与SYNTHESIS双宏/默认调试关闭两种配置通过 |

同一 CNN 融合用例：冷/热推理630/629周期、41次写回；关闭融合1812/1812周期、423次写回。
65536最大正累积、17元素奇数张量、16384元素8KiB满容量原地ReLU均通过。
冷/热缓存状态允许周期不同，验收检查输出、事件、计数所有权及模型语义，不要求周期相等。

课程网络验证为结构与整数结果检查：MNIST、KWS、HAR在4列/8列均通过；ECG的1D Pool继续按既有边界明确拒绝。
这些输入和输出不能替代数据集分类精度测试。

重跑命令（仓库根目录，Python与Icarus加入PATH）：

```powershell
python hardware/npu/scripts/run_program_regression.py --output build/int4_check --trained-model software/models/tinycnn8_flatten_strong.npz
python hardware/npu/scripts/run_program_regression.py --output build/int4_macro_check --macro-model build/private_ip/RA1SHD_2048x32M8.v --wave-disabled --trained-model software/models/tinycnn8_flatten_strong.npz
python hardware/npu/scripts/run_trace_configuration.py --output build/int4_trace_check
python hardware/npu/scripts/run_course_benchmarks.py --output build/int4_course_check --trained-model software/models/tinycnn8_flatten_strong.npz
python -m unittest discover -s software/tests -v
python -m unittest discover -s verification/fpga -v
```

Windows若Icarus出现DLL重定位错误，应在正常本地环境执行；临时目录可设为可写的build子目录。

## EDA 交接

唯一生产源文件表为 `hardware/soc/sim/filelists/chipdesign_nocpu.f`，顶层 `my_soc_top`；默认4×8。
Genus/DC沿用 `EDA_NPU_COLS=4|8` 和双宏开关；默认ASIC关闭trace。
独立物理流程新增 `--cols 4|8`，与 postlayout 生成的对应权重包保持一致；每个实验使用新目录，保存构建profile和源码指纹。
FPGA构建入口同样增加 `--cols`，本轮未完成该流程，不能宣称时序收敛。

最新die上限1950×1950μm已同步到物理流程候选筛选。旧报告的78.7%是扣除macro halo/电源环后的标准单元密度，
不能直接称作布线利用率；旧报告另有全局布线overflow指标。下一轮须分别检查单元密度、全局拥塞、详细DRC和STA。
本轮不对INT4实际面积、1950μm限制或时序达标作结论。
