# SoC 扩展：128 描述符、8×8 PE、共享双 SRAM

本次修改 RTL、编译器、主机协议、模型包和仿真；没有运行综合、STA、布局布线、bitstream 或板测。
当前接口为 ABI 5.0（`0x00050000`）。ABI 4 的物理实现与性能证据保留为历史记录，不能用于这版 RTL。
机器可读证据见 [soc_expansion_20261106.json](validation/soc_expansion_20261106.json)。

## 描述符容量

默认上限由 16 提升至 **128 条物理描述符**。同步扩展了 op_count、PC、错误算子编号、有效张量记录、
逐算子 trace、波形 PC 和主机校验。128 条、最后一条的错误定位（PC=127）均有实际回归。
融合后的算子才计入物理描述符数量，所以逻辑层数可以更多。

16 是旧控制字段和元数据规模的限制，并非算法要求。128 则对应现有存储的硬上限：
每条 64 B，8 KiB 模型 SRAM 最多装 128 条，而且描述符、权重、量化参数共享这 8 KiB。
128 条无权重 ReLU 可以运行；带权重网络必须满足 `64*N + packed_weights + params + alignment <= 8192`。
因此无法保证任意“小型模型”一定装得下，128 解决的是旧 16 条的人为限制，模型总字节数仍必须检查。
若要支持更长程序，需要更大模型存储或引入外部描述符/权重流式装载，以及相应的访问协议和状态保存；
简单删除比较条件会导致地址越界、截断或覆盖。

## 8×8 阵列

生产实例由 32 个 PE 改为 **64 个 PE**，每次 issue 消耗 8 个 K 元素。权重双 bank、8 行加载、
尾组零填充、depthwise 的对角权重、8 元素 gather、跨 kernel 边界寻址及延迟对齐均已适配。
组内和采用 11 位：`8*(-8)*(-8)=512`、`8*15*(-8)=-960` 均不能用原来的 signed 10 位表示。
完整累加/结果输出的语义保持原有精度。

普通权重 K 维补齐到 8，窄输出 tile 仍用 2 B/行；IC=1 的行填充 kernel 每行补到 8 个 K 元素。
零填充行直接向 PE 写零，不读取模型 SRAM。描述符 word15 为 `0x49340f80 | 8`。
ABI 5 固定 8×8，旧 c4/c8 二进制必须重新编译；七个发布模型的 c8 已更新，c4 目录仅保留 ABI 4 历史包。

## 两颗 SRAM 的实际使用

物理存储仍是 **2 × 8192 B、32-bit、单端口 SRAM**，没有添加第三颗 SRAM 或全张量寄存器数组。

| 统一 byte 地址 | 内容 |
|---|---|
| `[0,8192)` | 第一颗 SRAM，可放输入、中间值、输出 |
| `[8192,16384-model_bytes)` | 第二颗 SRAM 的空闲空间，自动用于激活 |
| `[16384-model_bytes,16384)` | 第二颗 SRAM 高端，受保护的模型镜像 |

模型端口继续使用从零开始的 byte offset，存储适配器自动重定位；激活端口内部仍用 nibble 地址。
编译器先生成模型镜像，得到确切 model_bytes，再保留模型区并按张量最后使用位置做 first-fit 分配。
目标在释放源之前分配，只允许已验证的精确原地 ReLU/Add。张量可以连续跨过两颗 SRAM 的边界。
输入和最终输出也可跨 SRAM；超过 `16384-model_bytes` 的峰值仍会拒绝，没有隐式溢出或外存交换。

模型和激活使用同一个读缓存、写合并队列、转发和 nibble RMW 通道，避免独立缓存的别名一致性问题。
模型读与激活读采用轮转仲裁，响应记录归属；消费者背压期间数据保持不变。物理调度每拍最多访问一颗宏，
因此两颗单端口 SRAM 都不会出现同拍冲突。写队列紧张时优先排空，层间、INPUT_COMMIT 和完成状态都等待 drain。
硬件预验证和控制事务检查禁止输入、源、第二源或目标覆盖模型区。RESET/超时取消在途状态并清除有效性。

实际通过的容量用例：未融合 TinyCNN 的激活峰值 **11680 B**；跨 SRAM 残差用例峰值 **12800 B**，
镜像 288 B；**12288 B 输入**原地 ReLU 跨两颗 SRAM。均冷/热执行两遍，逐逻辑写事务对比独立参考。

## 验证与性能边界

- 完整程序回归 **85/85**，逐写回比对 439154 次逻辑写事务；覆盖七算子、融合/独立、u4、depthwise、长 K、17/64/128 描述符、残差、JTAG 引脚、冷/热重复执行。
- 存储接口模型程序回归 **10/10**，使用公开的同步 2048×32 byte-enable 模型，覆盖两宏寻址、跨 SRAM、大张量及模型保护。
  elaboration 中实际只有两颗 `RA1SHD_2048x32M8`；这不是工艺时序模型或 foundry signoff。
- 块级验证含每种阵列列数的 1024 次 INT4/u4 极值与 bank/tag 检查、20000 量化向量、随机存储延迟和写背压、flush/drain。
- 非法描述符检查包括模型区覆盖、最后一条描述符错误定位和旧格式拒绝；生命周期检查含不完整上传、abort、timeout。
- trace 8 种配置通过；软件 INT4/trace 15 项、JTAG transport 3 项、性能接口 4 项通过。
- 七个发布模型重编译与 112 帧软件 golden 一致；新 RTL 对每模型第一个 golden 帧冷/热回放，不等同于完整数据集板测。

PE 数量翻倍不代表每个网络周期减半。共享物理调度采取保守串行访问，未并行利用两颗宏的独立端口；
小 K/窄通道网络还受权重装载、gather、量化/写回限制。对相同输入、融合开关和冷启动状态，
与本地 HEAD 导出的 ABI 4 基线比较的实际周期和加速比见 JSON 的 `baseline_comparison`。
长 K、TinyCNN、u4 链有收益；小 CNN 存在回退，不能把 64 PE 宣称为所有模型都更快。
后续可独立优化双 bank 并发访问，但需要保留跨 bank 读、RMW、缓存和完成条件的一致性。

## 复现与 EDA 交接

在仓库根目录，将 Python、`iverilog`、`vvp` 放入 PATH：

```sh
python hardware/npu/scripts/run_program_regression.py --packaged-models --jobs 4 --output build/soc_upgrade_verified
python hardware/npu/scripts/run_program_regression.py --quick --macro-model hardware/npu/tb/ra1shd_functional_model.sv --only cnn,full_activation_12k,spill_residual,descriptors_128,dwconv --jobs 4 --output build/soc_upgrade_verified_banked
python hardware/npu/scripts/run_trace_configuration.py --output build/soc_upgrade_trace_final
python software/zoo/check_golden.py
python -m unittest discover -s software/tests -p 'test_npu_*.py'
```

EDA 继续使用 `hardware/soc/sim/filelists/chipdesign_nocpu.f`；生产顶层默认已同步更新。
启用 `NPU_USE_RA1SHD_ACTIVATION` 后，两宏层级为：

```text
my_soc_top/i_control/i_activation/i_store/g_bank[0]/i_sram
my_soc_top/i_control/i_activation/i_store/g_bank[1]/i_sram
```

旧 `i_model_store/i_sram` 层级已不在生产实例树中，需由合作者更新宏绑定/约束/布局选择。
`npu_model_store.sv` 中的旧独立存储模块只保留给块级回归，生产使用同文件中的 `npu_shared_store`。
仍需重新评估 64 PE、128 条元数据检查和统一仲裁后的面积、时序及布线；本次不提供新的频率或 PPA 结论。

Trace ABI 也有更新：摘要窗口 `0x200..0x11ff`，波形窗口 `0x2000..0x5fff`，扩展版本 `0x00040080`；
采样字的 [127:120] 为完整 PC，算子/量化标志从 descriptor 摘要读取。主机工具已同步。
