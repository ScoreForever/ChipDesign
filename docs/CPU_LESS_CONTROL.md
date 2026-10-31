# 无 CPU 主机控制与模型 ABI 3.0（INT4）

本协议用于五算子可配置 NPU，不兼容旧固定 TinyCNN JTAG/MMIO ABI。
数据包仍由专用 JTAG TAP 传输；主机无需 RISC-V Debug Module。

## 传输与生命周期

IR=0x12，72-bit LSB-first 请求 `{opcode[7:0], address[31:0], data[31:0]}`。
响应 `{valid,busy,overrun,error[4:0],address[31:0],data[31:0]}`，opcode=0 轮询。
只有一条在途请求；忙时不得覆盖 mailbox。START/COMMIT 发生主机超时后不能自动重发，
应检查状态，因为原操作可能已经执行。TCK 可在推理过程中停止，系统时钟继续工作。

OpenOCD 主机扫描使用三个 LSB-first 字段，并显式指定十六进制前缀：
`drscan npu.tap 32 0x<data> 32 0x<address> 8 0x<opcode> -endstate IDLE`。
返回的三个字段按 data、address、status 顺序重新组成 72-bit 响应。不能将
`{packet:018x}` 生成的无前缀字符串直接当作 OpenOCD 十六进制参数：本机
OpenOCD 的数值解析器默认按十进制解释，可能把 READ 的 opcode 变成 0，
或拒绝含 a-f 的参数。2026-10-24 的 FPGA 实测据此修正了主机工具。

| opcode | 命令 | 作用 |
|---|---|---|
| 1 | READ | 读寄存器或空闲激活存储 |
| 2 | WRITE | 写配置、顺序模型数据或顺序输入数据 |
| 3 | MODEL_BEGIN | 立即使旧模型/输入失效，开始新事务 |
| 4 | MODEL_COMMIT | 检查完整性并预验证整个程序，成功后 model_valid |
| 5 | INPUT_BEGIN | 开始输入事务 |
| 6 | INPUT_COMMIT | 确认输入完整，并等待写缓冲刷新到激活 SRAM |
| 7 | START | 消费输入有效性，启动一次完整程序，返回 job ID |
| 8 | ACK | 空闲时释放完成结果所有权 |
| 9 | RESET | 中止、清有效性/故障，恢复默认 watchdog，RAM 不清零 |
| 10 | CLEAR_ERROR | 清诊断码；不解除需 RESET 恢复的 fault |

除 READ/WRITE 外 address/data 必须为 0；READ/WRITE 地址必须 4-byte 对齐。
结果未 ACK 时禁止新模型/输入写入及新任务；模型提交后可跨多次推理复用。
错误模型提交不会写激活存储。主机在 MODEL_BEGIN 前先完成本地检查，避免无谓使旧模型失效。

## 寄存器与窗口（byte address）

| 地址 | 含义 |
|---|---|
| 0x00 | ABI=0x00030000（3.0） |
| 0x04 | bit0 busy、1 model_valid、2 input_valid、3 done、4 model_loading、5 input_loading、6 fault |
| 0x08 | 最近错误码 |
| 0x0c | 支持算子位图=0x1f |
| 0x10 | 模型总 byte 数（4-byte 对齐） |
| 0x14 | 物理描述符数量（1..16） |
| 0x18 / 0x1c | 输入基址 / byte 长度 |
| 0x20 / 0x24 | 输出基址 / byte 长度 |
| 0x28 | watchdog 周期数，默认 10000000，不得为零 |
| 0x2c / 0x30 | 提交 / 完成 job ID |
| 0x34 | 故障物理 op index |
| 0x38 / 0x3c | 执行周期 / MAC 向量发射次数 |
| 0x40 / 0x44 / 0x48 | 激活逻辑写握手 / 模型读事务 / 激活读事务 |
| 0x4c | 控制器任务经过周期 |
| 0x50 / 0x54 | 激活 / 模型 byte 容量，当前默认均为8192 |
| 0x58 | 高16位阵列行数、低16位列数 |
| 0x5c / 0x60 | MAX_K / MAX_OPS |
| 0x64 | 输入 dtype：0=INT4、1=INT32，模型装载时必须配置 |
| 0x6c | 数值格式=0x0f200404：高到低四 byte 为 Q15 小数位数15、raw位宽32、权重位宽4、激活位宽4 |
| 0x10000 + offset | 模型写窗口 |
| 0x20000 + offset | 激活读/输入写窗口 |

MODEL_BEGIN 后先写完 0x10..0x24 六个配置及 0x64 输入 dtype，再从模型 offset=0 顺序写完整模型。
开始模型数据传输后配置不可再改；MODEL_COMMIT 会等待全部描述符/参数检查结束才应答。
INPUT_BEGIN 后从输入基址顺序写入，最后一个 word 不足 4 B 的部分按长度屏蔽。
WRITE 成功代表存储已按握手接收数据；INPUT_COMMIT 等待部分word和在途写入全部提交。
硬件每层结束及最终完成前也会等待激活存储排空，done/ACK不会早于结果写入完成。
RESET/超时取消缓存和队列，不清 SRAM 内容，之后必须重新完成模型和输入事务。
读输出按 32-bit 小端读取，主机截取准确 output_bytes；不足一 word 的高位不是有效输出。
INT4 按 NHWC 元素顺序打包：第一个元素在 byte 的低 nibble，第二个在高 nibble，按4-bit两补码解释。
奇数元素张量最后一个 byte 的高 nibble 必须为0，硬件输出会写0；主机工具拒绝非规范输入填充。
INT4 张量长度为 `ceil(H*W*C/2)` byte；INT32 为 `H*W*C*4` byte，禁止按每行或每通道另行补齐。
描述符、CSR 和主机窗口仍使用 byte 地址；计算核与激活存储之间使用 nibble 地址和8位 nibble 写掩码。

性能计数器和trace的寄存器布局、事件bit位置保留既有定义。激活写事件按
`w_valid && w_ready` 接受握手统计，背压期间保持valid不会重复计数。
`0x40`及性能bank的写次数是计算核逻辑写事务数；多个半字节写合并为一次物理SRAM写后，
该逻辑计数不会随之减少，不能把它直接当作物理SRAM写命令数。

波形trace是可选调试配置。`0x180`扩展版本始终存在；`0x200`起的逐算子摘要表也是可选配置，
`0x1a8`为16表示存在，为0表示本硬件未综合（ASIC默认，约0.2 mm²触发器），此时摘要窗口读返回地址错误2，
`trace(include_samples=False)`明确报未启用；24个全局性能计数器不受影响。
`0x184`为实际波形深度：1024表示启用，0表示本硬件未启用波形RAM。
深度0时采样count、first/last timestamp和captured control/offset为0；
flags只保留任务完成位bit2，started/full/truncated均为0，波形数据窗口读返回地址错误2。
`Npu.trace(include_samples=False)`仍可读取算子摘要；波形导出会明确报未启用，而非生成空波形。
ASIC综合默认关闭波形RAM，仿真/FPGA默认开启，构建时可显式覆盖。

## 64-byte 物理执行描述符

模型区从 offset=0 开始连续存放描述符，每条 16 个 little-endian uint32。
描述符后放权重和参数，二者都引用模型区 byte offset；不能引用描述符区。

| word | 内容 |
|---|---|
| 0 | bits7:0 opcode：Conv=1、FC=2、MaxPool=3、ReLU=4、Requant=5；flags 见下 |
| 1 / 2 | 激活源 / 目标 byte offset，4-byte 对齐 |
| 3 | 低16位输入 H、高16位输入 W |
| 4 | 低16位输入 C（FC 为展平长度）、高16位输出 C |
| 5 | 四个 byte：KH、KW、SH、SW；独立 Pool/ReLU/Requant 填0 |
| 6 | 四个 byte：pad top、left、bottom、right；非 MAC 填0 |
| 7 / 8 | 权重 / 参数 byte offset；不适用时填0 |
| 9 | bits3:0 为 signed INT4 clamp min，bits7:4 为 signed INT4 clamp max，高24位0；无 Requant 时填0 |
| 10 | 低16位最终输出 H、高16位最终输出 W（融合 Pool 后的尺寸） |
| 11..14 | 保留，必须为0 |
| 15 | 格式标识 `0x49340f40 \| COLS`，4列为0x49340f44，8列为0x49340f48；验证数值格式及权重布局 |

word0 flags：bit8 融合 Requant、bit9 融合 ReLU、bit10 融合 MaxPool，仅用于 Conv/FC；
bit11 源为 INT32（否则 INT4），bit12 目标为 INT32（否则 INT4）；其余位必须0。
独立 Requant 的量化由 opcode 隐含启用，不能重复设置融合 flags。
独立 MaxPool/ReLU 的输入输出 dtype 必须相同；Conv/FC 输入必须 INT4。
FC 输入 H=W=1、C=展平长度；展平顺序必须与权重一致，主机负责 NHWC 转换。

## 权重和参数打包

阵列 COLS=4 或 8。权重按 `[输出通道 tile][K 行][lane]` 存放，lane 为连续 nibble，低 nibble 优先。
K 按每四行补齐，未用输出 lane 和 K 行必须填0，权重区起始 offset 4-byte 对齐。
COLS=8 时每个 K 行为4 B；最后一个 tile 的真实输出通道数≤4时每行为2 B，高4个 lane 补0。
COLS=4 时每行为2 B。模型端口支持2-byte对齐的半字读取并返回到低16位，不增加第二个物理端口。
8列满 tile 的起址为 `weight_base + tile_index*groups*16`，末尾窄 tile 总长为 `groups*8` B。
ABI 2.x 的模型包须重新编译；主机按寄存器0拒绝版本不符，硬件还逐描述符检查 word15。
一般 Conv 的 K 顺序为 KH→KW→IC，FC 为连续输入特征。
特殊但与网络尺寸无关的快速布局：3×3 且 IC=1 时，每个 kernel 行存 `[kx0,kx1,kx2,0]`，
共三个四行组；由 kernel/IC 字段明确推导，不使用 TinyCNN 专用标志。

每个输出通道参数为两个 little-endian uint32，共8 B：word0是 signed INT32 bias，word1为
`multiplier[15:0] | ((shift & 63)<<16)`，bits31:22必须0。multiplier=0..32767（Q15），shift=-31..31，
bit15必须0，编码为-32的 shift 非法。Conv/FC 无量化时 word1填0；独立 Requant 的 word0填0。
逐张量 multiplier/shift 在主机展开为逐通道记录，硬件无需额外参数模式。

量化顺序为：INT32 饱和左移 `max(shift,0)`，计算 `floor((x*multiplier+16384)/32768)`，
按原 RoundingDivideByPOT 规则右移 `max(-shift,0)`，最后夹到 INT4 clamp。
负数右移的半整数按远离0处理；乘积阶段加半后取 floor。不是只截取 INT8 结果低4位。

## 错误码

1 opcode、2 地址、3 对齐、4 busy、5 生命周期、6 传输顺序、7 不完整、8 参数、
9 结果未 ACK、10 timeout；17 模型头、18 描述符字段、19 形状、20 范围/别名、
21 源数据未被已知区域覆盖、22 量化参数。
硬件检查物理区域和 dtype；主机负责编译后的逻辑图语义及内存生命周期。
运行中失败/超时不发布成功结果，RESET 后重装模型恢复。

## 工具使用

```powershell
python software/host/make_demo.py --output build/npu_demo
python software/host/npu_program.py build/npu_demo/model.json --output build/program
python software/host/npu_program.py build/npu_demo/model.json --no-fuse --output build/program_plain
```

JSON 模型输入为 `input: {name, shape:[H,W,C], dtype:"i4"或"i32"}（dtype 默认 i4）`，节点使用 `op,input,output`。
Conv/FC 权重为 `[OC,KH,KW,IC]` / `[OC,K]` 的嵌套数组或同顺序扁平数组；
Conv 另有 channels、kernel、stride（整数或 [SH,SW]）、pads，FC 有 channels；Requant 有 multiplier、shift、可选 clamp。
ReLU/MaxPool 按形状连接，不再需要固定层号。`make_demo.py` 生成完整示例。

已有 INT8 训练 NPZ 可由同一入口显式执行 `/16` PTQ 导入（需要 NumPy），权重/激活量化、bias与Q15系数按单位换算，
metadata 写入 `int4_conversion.accuracy_validated=false`。保留 labels，input_scale乘16；旧 FC common-scale 参数不再适用并删除。
硬件输出仍可为原始 INT32 logits。转换不保证模型精度，部署前须校准、评估，必要时进行 INT4 QAT。

```powershell
# 先按适配器配置启动 OpenOCD，加载 software/host/nocpu_openocd.cfg
python software/host/nocpu_jtag.py status
python software/host/nocpu_jtag.py infer --package build/program --input build/npu_demo/input.bin --output build/result.bin
```

`Npu.load_model(Package)` 先查询容量和阵列宽度；`Npu.infer(bytes)` 返回准确长度的输出 bytes。
`Npu.infer_values(values)` 按 manifest 检查逻辑元素数，自动打包输入并解码输出；也可直接使用 `pack_i4/unpack_i4`。
输出 dtype/shape 在 manifest 中，INT32 按小端有符号解码。预处理和分类由主机负责。
编译器默认按8 KiB激活容量分配；`--act-bytes` 可明确选择较小硬件配置。
模型包仍由描述符/权重/参数组成；JTAG 命令格式保留，数值与描述符 ABI 已升级为3.0。
