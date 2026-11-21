# 演讲者备注 / Speaker Notes

每页约 45–60 秒。中文为主讲稿，English 段落可用于英文答辩或问答。数值与 `data/numbers.tex` 一致，来源见 `README.md`。

## Slide 01

各位老师好，我们是 Ruijun He、Guoyi Zhang、Zhiyuan Zhao 和 Muyang Cheng。我们的题目是《先进芯片全栈设计与协同优化》。这个项目从一份可编程 INT4 NPU 的 RTL 出发，向下做到 TSMC 180 nm 的 1980 µm 物理签核，向上做到编译器、主机传输和 EGo1 FPGA 实板推理。今天我会按"硬件、软件、签核、实板、边界"的顺序讲，每一部分都给出归档的验证证据。

Good morning. Our project takes one programmable INT4 NPU from RTL to a 1980 µm TSMC 180 nm signoff, and up through a compiler, host transport and live EGo1 FPGA inference. Every number in this deck is generated from archived validation records.

## Slide 02

这一页是四个支柱的总览。第一，RTL 协同优化：8×8 INT4 MAC 阵列，加入写回流水与复位修复，RTL 审计 112/112 帧逐位一致。第二，编译器与主机：ResNet-20 主机后处理算子从 18 个降为 0，ResNet-18 tile 调用减少 24.5%，并修复了 FT232H 的 FIFO 死锁。第三，ASIC：4 个工艺角 50 MHz 全部零违例，DRC、LVS、天线、键合全部通过。第四，实板：57.143 MHz 下 833/833 次 zoo 推理逐位精确，ResNet-20 和 ResNet-18 也在实板上逐位一致。底部这句话是主线：瓶颈已经从计算转移到 USB/JTAG 传输。

Four pillars: RTL co-optimization, compiler and host transport, a zero-violation four-corner ASIC signoff, and live FPGA inference that is bit-exact everywhere. The remaining bottleneck is transport, not compute.

## Slide 03

这是系统架构。主机通过 FT232H 的 MPSSE 模式驱动 JTAG，TAP 每次移入一个 72 位命令包。TCK 域和系统时钟域之间是一个邮箱 CDC：请求和应答都用 toggle 信号经过两级同步器，复位异步置位、在两个域分别同步释放。命令控制器把描述符交给 npu_program_core，里面是 8×8 的 INT4 脉动阵列、Requant 和处理 Add/Pool 的 Element ALU。存储方面，两个 8 KB 的 RA1SHD 单口 SRAM 组成 16 KB 统一地址空间，激活和模型尾部共享，这就是 ABI 5.0 的契约，编译器严格按这个预算切块。

The host drives JTAG through FT232H MPSSE. A two-flop toggle mailbox crosses from TCK to the system clock. Two 8 KB SRAM macros form one 16 KB address space shared by activations and model data, which is the ABI 5.0 contract the compiler tiles against.

## Slide 04

这一页是三项 RTL 改动。PR 28 第一项：原来 psum 归约、加 bias、池化比较和写结果在同一拍完成，我们插入 ret2 级，先锁存归约结果，下一拍再加 bias。第二项：SRAM 读数据先寄存进 e_data_q 再进入 ALU，切断长组合路径；在 FPGA 宏下 MAC 标注 use_dsp，推断 DSP48E1。PR 30 是复位卫生：只改复位赋值，不改数据通路。为什么它修好了 ss125_max 的 X 泄漏？两态 RTL 仿真里未复位的寄存器读作 0，看不出问题；门级四态仿真里它们上电是 X，经写路径污染了 cache_tag[6]，导致命中判断和读回数据都变成 X。补上复位后双角 41 条 JTAG 命令全部通过。

PR 28 adds a writeback pipeline stage and a registered ALU operand, and infers DSP48E1 on FPGA. PR 30 resets the write queue and ret2 control registers; in four-state gate-level simulation their initial X had corrupted cache_tag[6]. With the reset added, both SDF corners pass.

## Slide 05

编译器流程是：INT4 QAT 的 ONNX 图，经过 IR 和模式融合，进入以 16 KB 为约束的权重驻留三维切块器，最后由向量化运行时执行，并用 NumPy einsum 参考逐位比对。两个关键优化：一是残差加法硬件化，用 OP_ADD 等于 7 的单描述符包，两个操作数放在 SRAM 的相邻对齐位置，NPU 内完成加权求和、Requant 和 ReLU。二是 Conv 加 MaxPool 融合：把池化输出分成无 padding 的内部区和 padding 边界条，内部区一个程序完成，边界条单独处理，保证逐位一致。

The compiler lowers INT4 QAT ONNX graphs into tiles that fit 16 KB. Residual adds run on the NPU through OP_ADD = 7, and Conv plus MaxPool are fused by splitting the pool output into an unpadded interior and padded border strips.

## Slide 06

主机传输层有四项优化：IR 驻留，相同 IR 不重复扫描；缓存 4 个 I/O 几何寄存器，推理时不再读回；写入按 512 个字拼成一次 USB 写；启动后前 3 ms 忙等，之后每次休眠 0.5 ms。任何异常都会清空缓存。右边是我们在 ResNet 实板运行中遇到的死锁：每个读字在 FT232H 中要排队 5 字节，旧默认 256 个字就是 1280 字节，超过了 1024 字节的硬件接收 FIFO，芯片停止执行命令，主机永远等不到数据。我们把读块改为 128 个字，即 640 字节，并在每个会话开始发送 RESET。zoo 和场景模型输出不超过 172 字节，所以以前没有暴露。

We cache the IR and I/O geometry, batch 512-word writes, and poll tightly for 3 ms. The deadlock came from 256-word read chunks queueing 1280 bytes into a 1024-byte RX FIFO; capping chunks at 128 words fixed it.

## Slide 07

这里是量化收益。左图是 ResNet-18 的三个阶段：基线 3141 次 tile 调用、878 个 group；只做软件融合降到 2316 次；再加硬件 Add 后是 2370 次 tile、855 个 group，tile 减少 24.5%，主机后处理从 27 个降到 11 个，JTAG 总量少 3.5%。右边 ResNet-20：主机后处理从 18 个降为 0，代价是单帧 JTAG 字节增加 32.6%，因为 Add 的两个操作数都要传，我们如实列出。下方是在 RTL 上验证的三个 ResNet-18 子图探针：p0000 是 stem 加 MaxPool 融合，p0011 是硬件 Add，p0026 是一个卷积，全部 0 位差。

For ResNet-18, tile calls drop from 3,141 to 2,370 and groups from 878 to 855. ResNet-20 goes from 18 host ops to zero at the cost of 32.6 percent more JTAG bytes. Three ResNet-18 subgraphs were verified bit-exact in RTL.

## Slide 08

这是最终版图。左边是布局图，两个 SRAM 对角放置；中间是电源网络。有效 die 是 1949.36 乘 1949.92 µm，加封环后的 postDummy GDS 恰好是 1980.0 乘 1980.0 µm，符合上限。布线后有 62,969 个标准单元，面积 1.325 mm²，两个 SRAM 宏 0.803 mm²，core 占用率 78.4%。综合面积中宏占 40.4%，触发器 28.3%，组合逻辑 31.3%；组合逻辑主要在 npu_program_core，占 68.6%。psum 累加器有 2304 个触发器，占触发器面积 21.5%。

The sealed die is exactly 1980 by 1980 µm, with 62,969 standard cells and two SRAM macros at 78.4 percent core utilization. Most combinational area sits in the program core, and the psum accumulators are a fifth of the flop area.

## Slide 09

时序方面，我们在 50 MHz 下做了 4 个工艺角的 MMMC，共 8 个 view，全部零违例、TNS 为 0。最紧的 setup 在 ss125，余量 0.369 ns，相当于 50.94 MHz；最紧的 hold 在 ffm40，0.052 ns。右上是 RC 敏感度：ss125 在 RC 放大到 1.2 倍时 setup 仍有 0.310 ns，ffm40 在 RC 缩小到 0.8 倍时 hold 仍有 0.051 ns。右下是两个 SRAM 宏在四个角的接口时序，最小余量 0.156 ns，全部为正。

All eight views close at 50 MHz with zero violations. The worst setup is 0.369 ns at ss125 and the worst hold 0.052 ns at ffm40, and both stay positive under plus or minus 20 percent RC scaling. SRAM interface timing is positive at every corner.

## Slide 10

签核验证有三部分。PrimeTime 在 TT 角的 setup 余量 8.914 ns、hold 0.185 ns，所有网络都有寄生标注；SRAM 内部是黑盒，接口由 Innovus 检查。Formality 证明布线后网表与 RTL 等价。门级 SDF 仿真在 ss125 最大延迟和 ffm40 最小延迟两个角各跑完整的 41 条 JTAG 命令，全部通过，没有致命错误。下面这条链是 X 泄漏的定位过程：读回 X、追到 cache_tag[6]、定位到未复位寄存器、修复、复测通过。底部注明了 SDF 的边界，第 18 页会详细说明。

PrimeTime shows 8.914 ns setup and 0.185 ns hold slack, Formality proves equivalence, and gate-level SDF simulation passes all 41 JTAG commands at both the slow-max and fast-min corners. The bottom row traces how we found and fixed the X leak.

## Slide 11

物理验证都在同一份 postDummy GDS 上完成。上面三张图分别是封环角部、8 层 dummy fill，以及键合 pad 的 keepout。DRC 执行 523 条规则，阻断违例为 0，6 类教学用的 IO、ESD、闩锁类别单独列出。天线 62 条规则和键合 64 条规则都是 0 违例。LVS 在 3.48% 电阻容差下 CORRECT，12 个端口全部匹配，SRAM 没有黑盒化。如果容差设为 0，会有 45 个 IO 电阻属性差，最大 3.47%。底部是 CO.R.4 修复：给一个 IO 单元的接触孔补了 PIMP 覆盖。

On one post-fill GDS, DRC has zero blocking violations across 523 rules, antenna and wirebond are clean, and LVS is CORRECT at a 3.48 percent resistor tolerance. We also patched a CO.R.4 PIMP coverage error in an IO cell.

## Slide 12

功耗用的是布线后网表、SDF 门级仿真产生的 VCD，5 个负载的注释率都是 100%。左图是内部功耗和翻转功耗，总功耗 22 到 45 mW，漏电只有约 37 µW；vectorless 估计是 63.25 mW，明显偏保守。右图是单次推理能量，用对数轴。重点在底部：同一个 CNN，融合版本周期从 1569 降到 518，平均功耗反而从 22.16 升到 27.51 mW，因为阵列更忙，但单次能量从 0.696 降到 0.286 µJ，节能 58.96%。

Power comes from fully annotated post-layout VCDs. Fusion raises average power because the array is busier, but it cuts cycles by 67 percent, so energy per inference drops by 58.96 percent.

## Slide 13

现在进入实板。EGo1 上 NPU 跑在 57.143 MHz。我们对 7 个 zoo 模型在 7 档 TCK、1 到 15 MHz 下各跑 17 次，一次冷加载加 16 次热推理，共 833 次，全部 0 位差。左图冷加载随 TCK 提升，几何均值从 127.5 降到 51.2 ms；中图热推理只从 24.5 降到 19.1 ms，说明单帧延迟主要由 USB 往返和轮询决定，而不是 TCK。右边表格解释了 FPGA 和行为级 RTL 的周期差：同步 BRAM 每次读多一拍，周期多 0.6% 到 3.9%，但输出逐位相同。

833 out of 833 live runs are bit-exact across seven models and seven TCK rates. Cold loads scale with TCK while warm inference barely moves, so USB round trips dominate. Synchronous BRAM adds 0.6 to 3.9 percent cycles without changing any output bit.

## Slide 14

这是六个演示场景：手写数字、关键词识别、心电、人体动作、人脸和表情。TCK 10 MHz 下 80 帧代表输入全部与 golden 输出一致，一致率 1.0。端到端中位延迟 17.67 到 20.42 ms，平均吞吐 48 到 56.7 fps，而 NPU 计算本身最多 1.29 ms。需要强调底部的范围说明：这些是演示场景公开的 golden 输入，检查的是输出逐位相等，不是数据集分类准确率。

All 80 representative frames across six demos match golden outputs. Median latency is 17.7 to 20.4 ms, while NPU compute is at most 1.29 ms. This measures output agreement, not classification accuracy.

## Slide 15

ResNet-20 在实板上跑了 5 帧，全部 bit_exact，max_abs_diff 为 0。标签正确 4 帧，第 3 号帧是模型本身误判，硬件输出仍与参考完全一致。每帧 42 个 group、51 次 tile、19 个融合子图、主机后处理 0 个，实测 JTAG 字节与计划完全相同。右图是时间拆分：983,666 个周期，即 17.21 ms 计算；但墙钟中位是 4264 ms，其中 99.49% 是 USB/JTAG 传输和完成轮询，有效带宽约 125.6 kB/s。

Five ResNet-20 frames are bit-exact on the board. Compute is 17.21 ms, but wall time is 4.26 seconds, and 99.49 percent of it is USB/JTAG transport.

## Slide 16

ResNet-18 是 ImageNet 级网络，我们在 16 KB 片上 SRAM 上运行。归档收据是 1 帧代表帧：bit_exact 为真，max_abs_diff 为 0，标签和预测都是 0。周期数 46,504,887，即 813.83 ms 计算，墙钟 150.57 秒。计划是 855 个 group，实测只做了 852 次模型加载，因为运行时检测到相同模型已驻留，所以实测 24,354,093 字节，比计划的 24,357,357 少 3,264 字节。右边诊断：传输占 99.23%，是计算的 183.6 倍；要把每帧 24.35 MB 藏到计算之下，需要约 30 MB/s。最后要说明：我们只报告 1 帧，多帧压力测试中遇到的 USB 超时没有归档，不计入结论。

One archived ResNet-18 frame is bit-exact. Compute is 813.83 ms against 150.57 s wall time. Model residency reuse makes 852 loads instead of 855 planned groups. The system is transport-bound by a factor of about 184, and we report only the single archived frame.

## Slide 17

这一页把三个平台放在一起比较。同一份 RTL：行为级参考、57.143 MHz 的 FPGA 实板、以及 50 MHz 的 ASIC 签核。以 ecg 为例：行为级 14,024 周期；FPGA 14,571 周期，即 255 µs；ASIC 与行为级周期相同，280.5 µs，功耗 39.99 mW，能量 11.22 µJ。ResNet 在 ASIC 上的时间是按周期除以 50 MHz 折算的，我们没有做门级功耗仿真，表里如实标注。结论是：三个平台逐位一致，差别只在周期和时钟。

The same RTL runs as a behavioral reference, on the FPGA, and as a signed-off ASIC. All three are bit-identical. FPGA cycles are slightly higher because of BRAM, and the ResNet ASIC times are cycle-based estimates, not measured power.

## Slide 18

这一页是我们认为最重要的：哪些还没做到。左边是开放边界审计，formal_tapeout_signoff 为 false。ERC 有 7 个层次标记、977 个展开标记，都在 IO 和封环周边，下图标出了位置。LVS 需要 3.48% 的电阻容差。DRC 有 6 类教学豁免。SDF 仿真关闭了时序检查，每个角有 64 条 SRAM CLK 到 Q 的路径未限定，ffm40_min 有 140,805 条负延迟被钳位为 0。RC 是 ±20% 代理缩放，还没有独立 xRC SPEF 和 IR 压降分析。实板方面，场景是代表帧，ResNet-18 只有 1 帧，长时间 USB/IP 稳定性未验证。右边是路线图：首先用 QSPI 加 DMA 取代 JTAG 数据通路，释放 ResNet-20 17.2 ms、ResNet-18 814 ms 的计算能力；其次补齐 xRC、Voltus IR/EM、SDF 时序检查和 LVS、ERC 的闭合；最后在原生 Linux 主机上做多帧压力测试。

This slide lists what is not yet signed off: ERC markers at the IO ring, a 3.48 percent LVS tolerance, teaching DRC waivers, SDF without timing checks, proxy RC, no IR-drop analysis, and limited board sample sizes. The roadmap replaces JTAG with QSPI and DMA, then closes the remaining signoff steps.

## Slide 19

总结四点。第一，RTL 协同优化让三个平台逐位一致。第二，编译器和主机把残差和融合下沉到硬件，并修复了 FIFO 死锁。第三，1980 µm 的 ASIC 在 4 个角 50 MHz 零违例，物理验证通过，融合节能 58.96%。第四，实板上 zoo、场景、ResNet-20、ResNet-18 全部 0 位差，瓶颈是传输。右边是证据索引，所有数字都能在这些目录中找到。谢谢各位老师，欢迎提问。

To conclude: bit-exact RTL across three platforms, compiler and host co-optimization, a zero-violation 1980 µm signoff, and bit-exact live inference up to ResNet-18. The evidence index lists where every number comes from. Thank you, we welcome your questions.
