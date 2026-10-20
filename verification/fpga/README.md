# main + H16 FPGA 验证工作流

> 2026-10-20 后续修复见 [CDC 与门禁修复记录](../../docs/FPGA_CDC_CLOSURE_20261020.md)。
> 当前执行器要求 schema=2 和同次构建的 `build_identity.json`。下方示例使用 CDC 修复后的新构建；schema=1 旧证据仅供历史追溯，不回填升级。TIMING-18 仍阻止完整签核。

本工作流把“生成向量 → 明确烧写对象 → CPU/GDB → RTL/板上比对 → 复位恢复 → 实现审计 → 汇总门禁”串起来。
这是有限的定向和随机回归，不提供 RTL 穷尽证明。任何阶段失败、文件哈希变化、缺失用例都不能签核；功能通过也不能自动豁免 CDC。

当前CDC修复版的[结果汇总](results/2026-10-20-cdc/report.md)和[机器汇总](results/2026-10-20-cdc/report.json)为功能PASS、实现BLOCKED（TIMING-18）。修复前的[历史快照](results/2026-10-20/report.md)另行保留。完整证据保存在本机build目录，未包含在仓库中；哈希及时间对应验证当时版本。

当前验证对象：以 `main` 提交 `9d25b0965ba4c190c4ec352d4f4b9b5b5920273b` 为基线，加本地 CDC 复位/约束修复，以及 **TCK → J5 第21脚 / H16**。
TDI18、TDO19、TMS20、GND33/34、VREF35/36；第17脚留空。系统50MHz，TCK约束1MHz，测试适配器500kHz。
CPU/NPU计算逻辑未改，JTAG复位同步、同步链属性及约束已修改并重新实现。改变 RTL 或约束必须重新构建、重新烧写并生成新的验证目录，不能复用旧结果。

## 环境与执行

从仓库根目录运行 PowerShell。需要 Vivado、ModelSim、Icarus、Windows Python（用于现有 RTL 脚本）、WSL Python+NumPy、xPack RISC-V GCC/GDB、FTDI版OpenOCD。
本机默认路径写在 `run.ps1` 参数中，可用 `-Vivado`、`-Python`、`-Distro` 覆盖。先用 `scripts/lab6/lab6.ps1 -Action Check` 检查。

下面是可重跑的顺序；为每次正式验证选择**新的** `$out`。比特流路径与哈希必须显式提供。默认不自动构建或自动猜测烧写对象。
若修改硬件源码，先运行 `./scripts/lab6/lab6.ps1 -Action Build`，审阅新构建的 `summary.md` 与实现报告，再将下面的bit/dcp/hash替换为该次构建；禁止继续使用示例中的旧bit。

```powershell
$out = 'build/fpga_validation_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
$bit = 'build/fpga_cdc_closure_20261020_r2/fpga_top.bit'
$dcp = 'build/fpga_cdc_closure_20261020_r2/route.dcp'
$hash = 'daf0bdd6f7e264864b3203330848f612f1e13afe50e39d5b875673977f237929'

./verification/fpga/run.ps1 -Stage Prepare -Output $out -Soak 100
./verification/fpga/run.ps1 -Stage Program -Output $out -Bitstream $bit -BitstreamSha256 $hash -CableSerial '1234-tulA'
```

烧写使用同目录 `summary.md` 的构建验收结果，检查器件、DONE和CRC，生成 `deployment.json`；它是烧写命令和文件身份记录，不等价于配置帧逐位回读证明。
独立终端中先转发外部FT232H并运行OpenOCD。USB BUSID需先用 `UsbList` 获取；本机此前为1-3。

```powershell
./scripts/lab6/lab6.ps1 -Action UsbList
./scripts/lab6/lab6.ps1 -Action Attach -BusId '1-3'
./scripts/lab6/lab6.ps1 -Action OpenOcd -Target cpu
```

保持OpenOCD运行，回原终端：

```powershell
./verification/fpga/run.ps1 -Stage Run -Output $out -Bitstream $bit -BitstreamSha256 $hash -Checkpoint $dcp
```

`Run` 依次执行 Gdb、Rtl、Board、Recovery、Audit、Report。也能用 `-Stage` 单独执行尚未执行的阶段。
已有日志/结果目录会被拒绝覆盖，避免将不同运行混为一份。发现失败后保留证据，修复后使用新目录；不要手改失败结果。
如果重新上电、重新配置FPGA或更改接线，先重新烧写，之后再运行全部板上阶段。程序装入易失SRAM，测试结束CPU保持halt。

## 分层检查及验收标准

| 层 | 实际检查 | 通过条件 |
|---|---|---|
| 构建身份 | commit、完整硬件输入及测试固件源码快照、固件/向量/模型/bit/dcp SHA256 | 文件与清单一致；烧写记录绑定该清单 |
| GDB | CPU halt、BootROM首字、SRAM读写、`addi t0,t0,1`单步 | `t0:41→42`，恢复暂存寄存器/内存，输出PASS标记 |
| NPU板上 | 43组，类数1..8，非零权重、随机/脉冲/极值、量化边界、训练权重 | CPU运行真实MMIO固件；所有8个logit与独立整数golden逐位相等 |
| 传输 | CPU字节、CPU packed、DMA packed、DMA模型权重 | 三种路径同样通过；DMA DONE/清除及输入pending等待正确 |
| 连续任务 | 每组通常2次、模型复用100次 | 每次重装输入，全部完成、结果一致；issues==retires，profiler有效 |
| 负向MMIO | 类数非法、只读/保留地址、权重顺序、非法量化参数、忙时改类数/权重/输入/重复START | 指定错误码、清错恢复、模型与输出未被破坏 |
| CPU/IRQ | CPU 512次scratch读回、字节/半字写；NPU `mip[16]`、DMA `mip[17]` | 实际CPU读写通过，IRQ pending和clear均可见；全局中断保持关闭 |
| SRAM恢复 | 整个8KiB：全0/全1/交替/行走位/地址/随机，原内容备份恢复 | JTAG写入、verify、全区dump逐字节一致 |
| 复位恢复 | 6轮DM ndmreset，交替空闲与长任务运行中打断 | NPU/DMA控制状态复位，再装载固件执行2次并比对golden |
| NPU RTL | 7个单元配置、21组golden、Pool1/Pool2/logits、独立事件轨迹、10种故障注入检查器 | 现有脚本明确PASS，逐元素/调度/计数器检查通过 |
| SoC RTL | FPGA内存配置，完整SoC、DMA尾响应屏障、SRAM读延迟1/3、背压及未决请求复位 | ModelSim结构回归7个运行均PASS |
| 实现审计 | 实际route.dcp的CDC、时钟交互、例外覆盖、setup/hold、DRC、methodology | 未关闭的关键CDC/约束问题阻止完整签核 |

CPU固件不包含预期输出，期望值只存在主机端。独立golden采用规范卷积循环与整数语义；训练权重样本额外与NumPy参考实现交叉核对。
板上观察最终logits，Pool1/Pool2内部值由RTL回归检查。训练权重输入是合成特征，不代表真实语音分类准确率。
保存的32字原始CPU结果会由 `report.py` 再次解码、比对清单和JSON，避免只凭控制台“PASS”。

## 产物与门禁

- `manifest.json`、`source_snapshot/`：向量、源码、工具链、提交及哈希；每组保留规范模型JSON和CPU配置bin。
- `validation.elf/bin/map/dump`：可审计CPU程序及编译日志。代码占SRAM前4KiB，配置在`0x80001000`，结果32字位于`0x80001d00`。
- `program/`、`deployment.json`、`gdb.log`：烧写/CRC/DONE及GDB证据。
- `board/*.bin`、`board/summary.json`：逐组原始读回、logits、性能与负向/IRQ计数。
- `recovery/`：全区内存模式、完整读回、备份和每轮复位恢复状态。
- `rtl_*`：两个RTL回归的日志与绑定清单的运行记录。
- `audit/`：route.dcp身份、CDC/时序/DRC/方法学报告。
- `report.json/md`：对上述证据再次校验后生成的最终结果。

`report.py` 退出码：0=声明范围内门禁通过；1=失败/证据不完整/身份不一致；2=功能证据通过但实现签核仍有阻塞。
PowerShell遇到非0会抛错并停止。当前设计**预期返回2**，这不应通过屏蔽告警改为0。
重读已有结果可执行：

```powershell
& 'C:/Users/Lenovo/.conda/envs/ai_env/python.exe' verification/fpga/report.py --output build/fpga_validation_cdc_20261020_r3
```

## 实现签核状态

1. 已关闭旧网表 `CDC-7 Critical 23`：JTAG复位改为TCK域同步释放；新网表此项为0。
2. 已关闭 `CDC-2 Warning 2`：应答同步级已标记 `ASYNC_REG`，新网表此项为0。
3. 已关闭异步时钟组切断DMI最大延迟的问题：现按data/req/ack六组端点约束并检查实际路径，全部满足20ns上限；保留75条CDC-15握手结构告警可见，不等于协议形式证明。
4. 仍未关闭 `TIMING-18`：外部IO延迟假设未完整表达，需要依据下载器、TCK采样边沿和板级路径建立输入/输出约束。LED等非时序接口也应作有依据的分类。

后续修复仍应闭环：修复 → 新bit/dcp → 新烧写记录 → 全量工作流 → CDC/时序复审。测试本身的剩余缺口见[测试审查](../../docs/FPGA_TEST_REVIEW_20261020.md)。
尚未开展形式证明/等价检查、完整CPU ISA符合性、功能/代码覆盖率收敛、布局后时序仿真、温压/电气裕量、物理按键/PLL丢锁测试及真实语音数据准确率。有限回归通过不能替代这些验证。
