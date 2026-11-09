# NPU 架构（INT4 / ABI 5.0）

ABI 4 冻结基线见 [main baseline](BASELINE_MAIN_20261106.md)，ABI 5 改动与验证见 [SoC 扩展记录](SOC_EXPANSION_20261106.md)，完整编码与形状限制以
[ABI 5.0 协议](CPU_LESS_CONTROL.md)为准，ABI 4 历史物理实现见 [INT4 v2 报告](INT4_V2_REPORT.md)。

## 默认实例树

主机负责训练、预处理、量化、静态图编译和存储分配；芯片不含指令 CPU，不解析 JSON。

```text
my_soc_top
├─ npu_jtag_transport：IR12 普通命令、IR13/14 突发传输、握手邮箱 CDC
└─ npu_control_subsystem
   ├─ npu_command_controller：模型/输入事务、完成保护、超时与复位
   ├─ npu_shared_store：模型/激活轮转读仲裁与响应归属
   │  └─ npu_activation_store：两颗 8 KiB SRAM、统一缓存和写队列
   ├─ npu_trace_capture：可选逐算子摘要与 16 KiB 波形存储
   └─ npu_program_core：预验证、描述符调度、算子执行
      ├─ npu_mac_array：共享 8×8 MAC、双权重 bank
      └─ requant_lane：双 lane 半字节输出、单次舍入
```

默认源文件表为 `hardware/soc/sim/filelists/chipdesign_nocpu.f`。ABI 5 固定为 8×8；
`NPU_TILE=8` 是空间 tile 选项，不表示 8×8 MAC。旧模型必须重编译。当前扩展的存储图、容量、验证和限制见 [扩展记录](SOC_EXPANSION_20261106.md)。
CPU、AXI 和旧课程代码不属于默认实例树。

## 算子和数值

- Conv：普通/深度卷积、1–7 矩形核及 1D；具体 stride、padding、对齐和容量条件由协议限定。
- FC：与 Conv 共用 MAC；权重为 signed i4，输入可为 i4/u4。
- MaxPool、AvgPool/GAP：一般窗口；AvgPool 输出整数和，平均比例并入后续 Requant。
- Add：同形状 i4/u4 输入，计算 `wa*a + wb*b`，支持满足协议条件的原地写回。
- ReLU、Requant：支持独立执行和合法后处理融合；不支持的配置在提交时拒绝。

i4/u4 每 byte 两个元素，低 nibble 优先；原始张量为 i32。
每通道参数为一个 32-bit 字：signed bias 18 bit、Q8 multiplier 8 bit、shift 6 bit。
JSON 接受 Q15 multiplier，编译器规范化为 Q8，执行采用饱和左移后的单次舍入与 clamp。
ABI 2.x/3.0 二进制包不能复用，旧双次舍入精度结论也不适用。

## 融合与存储

编译器只省略单消费者且非模型输出的合法中间值，可按融合类型或节点关闭融合。
Conv/FC 可接池化、Requant、ReLU；Pool/Add 支持合法的 Requant/ReLU 后处理。
Conv→Conv 不融合，中间激活正常写回；关闭融合可能增加 i32 中间张量峰值；当前分配器可使用两颗 SRAM 的连续空闲空间。
结果槽、ready/valid 背压及层边界 drain 保证在途计算和写队列正确提交。

描述符、权重、参数占最多 8 KiB，放在第二颗 SRAM 高端；输入、中间值和输出共享低端 `16384-model_bytes` B。
最多 128 条物理描述符，执行地址为编译后的 byte offset，不做 tensor-ID 间接查表。
主机分析生命周期，硬件预验证形状、类型、有效区域和边界；成功才设置 model_valid。
仅协议允许的精确区域 ReLU/Add 可以原地执行。

两颗单端口 32-bit SRAM 由统一控制器访问，模型读与激活读轮转仲裁；当前保守实现每拍最多访问一颗宏。激活读缓存、写合并及转发保持 RAW 顺序；
孤立 nibble 写在仅有 byte 写使能的宏上使用读改写。提交和层边界等待必要的写入落地。
运行中锁定模型/输入，完成输出保留到 ACK；RESET/超时取消在途状态并清有效性，不清 RAM。
JTAG 只允许一条在途事务，超时后须检查状态，不能盲目重复 START/COMMIT。

## 平台和验证边界

ASIC 用 `NPU_USE_RA1SHD_MODEL`、`NPU_USE_RA1SHD_ACTIVATION` 实例化两颗
`RA1SHD_2048x32M8`。公开行为存储用于仿真/FPGA 推断，不能当作硬宏面积证据。
ASIC 和生产 FPGA 构建默认关闭波形及逐算子 trace；普通仿真默认开启。
FPGA 脚本可用 `CHIPDESIGN_TRACE=1` 请求调试配置，须重新验证时序和资源。
24 项全局性能计数器保留，主机按能力寄存器判断 trace 是否可用。

ABI 4 历史 ASIC 选择 `diagonal_facing_h25`：1920 µm die，两颗宏相对角放置，TT corner 签核。
ABI 4 历史 FPGA 为 50 MHz、4×8、trace-off，setup WNS 0.000 ns，不能宣称充足裕量。
112 帧 / 224 次实板验证与 67,245 次软件完整测试集评分属于不同验证层。
物理按键、断电恢复、电气波形、多角 STA、IR/EM 等缺项见 baseline 记录。
