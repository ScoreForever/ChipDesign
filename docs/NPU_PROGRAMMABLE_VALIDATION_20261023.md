# 可编程 NPU 实现与验证

日期：2026-10-23。实现基线为 `exp/no_cpu` / `6957d2c`，后续提交记录以 Git 历史为准。
本轮实现新 ABI 2.0，不兼容旧固定 TinyCNN MMIO/JTAG 格式。
机器可读证据：`docs/validation/npu_programmable_20261023.json`。

## 实际交付

- 一套共享描述符执行核，五算子 Conv、FC、MaxPool、ReLU、Requant。
- 可配置形状、通道、卷积步幅/四侧 padding、权重、bias 与逐通道量化参数。
- 独立 INT8/INT32 Pool/ReLU，独立 INT8/INT32→INT8 Requant；主机输入也可为 INT32。
- Conv/FC 的后处理融合、结果槽预留、直接数据传递；独立路径复用同一后端。
- 统一 8 KiB 模型存储、4 KiB 激活空间、当前参数 tile 缓存；最多16条物理描述符。
- ABI 2 控制器、模型预验证、模型/输入完整性、忙时保护、结果 ACK、超时、中止和错误 op 定位。
- Python 模型编译/参考执行/主机驱动、完整示例、已有训练 NPZ 导入、新 CI 和唯一默认文件表。
- 移除固定 TinyCNN 核、重复算子 RTL、CPU/MMIO NPU 集成与旧 ABI 主测试入口。
  旧模型黄金算法保留作数值对照，不属于生产路径；原有两篇未跟踪的2026-10-22分析文档未改动。

设计细节见 `docs/ARCHITECTURE.md`，二进制格式和控制协议见 `docs/CPU_LESS_CONTROL.md`。

## 功能证据

完整回归：

```powershell
python hardware/npu/scripts/run_program_regression.py --output build/program_owner_verified --trained-model software/models/tinycnn8_flatten_strong.npz
```

结果：**58 个场景全部通过，每个连续执行两次推理**。
包括4×8/4×4两种阵列；融合/非融合；CNN、多层FC、Pool开头、INT32 Pool、
完整4 KiB原地ReLU、小于kernel的输入、单元素归约、全负Pool、独立H/W步幅及非对称padding、非整倍数K/通道、逐通道量化极值、K=257精确归约、
原TinyCNN及真实训练模型、主机直接INT32 Requant/Pool。JTAG引脚覆盖小CNN的融合/独立两种模式。

参考模型执行原始逻辑节点，不解析硬件描述符或权重打包。逐次比较 **47204 次实际张量写回事件**
的地址、掩码和数据，包括之后被生命周期复用覆盖的中间结果；同时从真实控制接口读回最终输出。
所有场景检查命令响应背压，避免请求被重复执行。

另外通过：

- **20000 个 Q31 向量**：独立 Python 商/余数黄金模型，II=1，检查数据与tag顺序。
- 模型存储的响应背压、在途复位、复位不破坏内容；激活存储的掩码写入、无对齐读取与响应保持。
- 独立可变延迟存储测试台：随机请求许可和1–8拍响应延迟，融合与独立路径均通过。
- 8类非法模型拒绝：opcode、保留字段、步幅、输出形状、目标越界、未知源、负multiplier和非法shift。
- 生命周期测试：缺输入START、输入/模型不完整、乱序写入、忙时读写、重复START、结果未ACK、中途RESET、watchdog。
- 真实课程 `RA1SHD_2048x32M8.v` 模型下的 **27 个原生接口场景**、量化与存储单元测试。
  私有工艺模型仅从 `build/private_ip/` 使用，不提交或复制其内容。

课程宏复现：

```powershell
python hardware/npu/scripts/run_program_regression.py --quick --macro-model build/private_ip/RA1SHD_2048x32M8.v --output build/program_owner_macro
```

Windows沙箱内Icarus可能触发WinError 623；本轮以获准的普通本机执行环境运行。
并行EDA时一轮JTAG仿真曾超过旧240秒主机进程限制；相同RTL的完整58场景通过，
入口现为vvp保留600秒预算，并在超时时保存部分日志。
功能通过不等于芯片时序、板级JTAG、bitstream或工艺宏物理视图已经验证。

## 融合的实际收益

以下是RTL测得周期，不含模型/输入装载，不是板上端到端时间。
小CNN：`4×6×3 → Conv3×3/5ch → Requant → ReLU → Pool → FC/11ch → Requant → ReLU`。
同一4×8后端，逻辑权重、输入、数值结果相同：

| 配置 | 周期 | 激活写事务 | 模型byte数 | 激活分配峰值 |
|---|---:|---:|---:|---:|
| 融合 | 764 | 41 | 1060 | 102 B |
| 独立 | 2069 | 423 | 1568 | 672 B |

融合周期下降约63.1%，写事务减少约90.3%。写事务不是byte数：独立模式有INT32写，
不能把事务降幅直接当作总线byte流量降幅。两个配置复用同一硬件，没有为融合新增第二套算术单元。

## 对旧固定核的代价

原TinyCNN（8类、极值输入）与旧独立黄金模型的Pool1、Pool2、logits一致，
RTL也逐次检查全部实际Pool1/Pool2/logits写回：

| 后端 | 周期 | 说明 |
|---|---:|---|
| 旧固定4×8 SRAM核 | 3028 | 2026-10-20既有证据，本次未重新综合旧核 |
| 新可编程4×8 | 3470 | 当前源码实测，约+14.6% |
| 新可编程4×4 | 6256 | 当前源码实测，较新4×8约+80.3% |
| 新可编程4×8、真实训练4类模型 | 3442 | 当前源码实测 |

新增描述符与参数访问、通用边界/地址准备有成本；本轮没有把通用化包装成所有网络的加速。
开发中同步读取的串行首版曾为11839周期，连续激活预取、单通道3×3行打包、
权重返回与下一请求重叠已明显降低代价。最终版本把步长和kernel偏移预计算移出发射热路径，
以少量层/归约组准备周期换取更短的组合逻辑。

原网络若关闭全部融合，第一层INT32输出需10240 B，超过4 KiB；编译器明确拒绝，
不会静默截断或依靠未实现的外存。扩大逻辑容量必须对应重新构建的硬件及实际RAM映射。

## 连续Conv流式探索

```powershell
python software/host/explore_conv_streaming.py --output build/conv_streaming.json
```

两层3×3 Conv+Requant+ReLU的行缓存软件执行与完整张量参考逐元素一致。
8×8、8×16、8×32的三组实验中，第一层中间值缓存从8行减为3行（例如256→96 B），
但8×8时按当前spatial tile计的权重装载量从1792增至3584 B，
层间模式切换从1次增至13次；宽度16/32时权重装载估算不增加，切换开销仍存在。

因此当前生产RTL支持连续Conv组合，但层间INT8结果仍写存储。
本轮交付了行缓存可行性和成本对照，**未实现Conv→Conv硬件串流模式**，
没有声称这个软件实验测得RTL周期或PPA。

## FPGA 综合与布局布线

Vivado 2025.2，EGo1 `xc7a35tcsg324-1`，完整 `fpga_top`（4×8 NPU、控制器、JTAG、Clocking Wizard及LED），
核心50 MHz、TCK 1 MHz。综合检查点、RTL回归与当前源码的SHA-256逐项一致。

| 阶段 | LUT | FF | DSP | BRAM tile | WNS | TNS | WHS |
|---|---:|---:|---:|---:|---:|---:|---:|
| 综合 | 8906 | 4491 | 48 | 4 | −1.394 ns | −17.946 ns | +0.045 ns |
| 首轮布线 | 8569 | 4491 | 48 | 4 | −0.178 ns | 有违例 | +0.031 ns |
| 布线后物理优化并重新布线 | **8572** | **4491** | **48** | **4** | **+0.073 ns** | **0 ns** | **+0.031 ns** |

最终现有约束全部满足，DRC错误0；综合的配置/BRAM专项DRC也通过。
FPGA的48个DSP为整个系统资源数，不能只用32个MAC推算全部DSP占用。
这不是与旧核在相同条件下重新综合后的面积对照，不据此声称通用化后总面积下降。

复现入口（输出目录必须尚不存在）：

```powershell
python hardware/fpga/scripts/build_nocpu.py --vivado-root D:/Project/Vivado/2025.2/Vivado --output build/program_vivado_owner_retry --jobs 4
python hardware/fpga/scripts/implement_nocpu.py --vivado-root D:/Project/Vivado/2025.2/Vivado --synthesis-dir build/program_vivado_owner_retry --output build/program_route_final
```

第二条先核对综合检查点的源文件哈希，再执行布局布线；若建立时间仍负，增加一次
`phys_opt_design -directive AggressiveExplore` 后重新布线。任一时序/DRC错误都会使入口返回失败。
没有降频或新增时序豁免。脚本保留EGo1非专用TCK时钟脚的条件恢复分支：仅实际触发Place 30-574时
才对TCK网络应用 `CLOCK_DEDICATED_ROUTE FALSE`；本次最终构建未触发该分支，未应用此属性。

**余量与边界：** +0.073 ns余量很小，后续RTL或工具变化应重跑。
现有约束有3个输入缺input delay、3个输出缺output delay，外部JTAG/板级I/O时序尚未完整签核；
未生成bitstream、未烧板、未进行板上推理或SDF后仿。ASIC工艺的面积/时序/功耗仍未验证。

## 源码与验证边界

默认源文件表9项，对应NPU RTL仅5个文件。公开证据保存默认回归的源码SHA-256，
便于区分实现变化和旧实验。Genus/DC/FPGA入口已接入新文件表与模型宏定义。
未运行ASIC综合/PnR、ASIC DRC/antenna、SDF后仿、bitstream下载或物理USB-JTAG推理。
FPGA综合/布线证据单独记录，不与功能周期或ASIC面积混用。
