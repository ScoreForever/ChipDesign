# ABI 5.0 NPU 性能与 50 MHz 验收报告

最终采用坐标范围预计算版 RTL，加一轮额外 setup 目标的后端优化。ABI 5.0、8×8 MAC 阵列、128 条描述符上限和模型包格式保持兼容。当前工作区的 RTL 已逐文件核对，与最终镜像源码文本一致。

最终 50 MHz 布线后 WNS +0.344 ns、WHS +0.058 ns，TNS/THS 均为 0。上一轮已上板版为 +0.014/+0.040 ns。LUT 为 18,961 / 20,800（91.16%），相比上一轮增加 765 LUT。BRAM 4、DSP 16，均在 xc7a35tcsg324-1 容量内。

七模型首帧各运行两次，共 14 次，输出逐字节匹配已发布 golden，硬件周期与生产配置 RTL 回归完全一致。暂停 JTAG 扫描、busy 重复 START、busy 协议 RESET、watchdog 超时及恢复后推理共五项板级检查通过。TCK 配置为 1 MHz。实体复位按键和电气波形属于后续人工检查项。

## 七模型周期

比较输入、模型二进制和 golden 写回事件均与上一轮相同，哈希核对记录见 `benchmark_input_identity.json`。原始 RTL 和上一轮镜像列来自 RTL 回归，最终列已由 EGo1 实测核对。每个模型使用第一帧 golden，冷/热两次重复；覆盖范围为这些样本及独立功能回归。

| 模型 | 原始 RTL | 上一轮镜像 RTL | 最终实板 | 较上一轮 | 较原始 |
|---|---:|---:|---:|---:|---:|
| ecg | 14,478 | 23,645 | 15,857 | -32.94% | +9.52% |
| fashion_cnn | 42,363 | 90,872 | 47,204 | -48.05% | +11.43% |
| gtsrb_l | 118,009 | 205,597 | 128,283 | -37.60% | +8.71% |
| har | 16,366 | 28,841 | 18,836 | -34.69% | +15.09% |
| kws_l | 81,307 | 147,308 | 86,626 | -41.19% | +6.54% |
| kws_m | 83,201 | 149,233 | 88,821 | -40.48% | +6.75% |
| mnist_l | 34,137 | 62,946 | 37,086 | -41.08% | +8.64% |

原始 RTL 对应的 EGo1 构建在容量检查阶段终止。它作为周期参考。最终版较上一轮镜像减少约 33%–48% 的周期，相对原始 RTL 的额外开销缩小至约 6.5%–15.1%。

## 63%–115% 回退的原因

同一 Fashion-CNN 的版本分解如下。按迭代顺序计算，加入共享读请求缓冲这一步贡献了七模型额外周期的约 69%–79%。该比例来自固定输入的相邻版本差值，反映这组实现之间的变化。

| 阶段 | 总周期 | 较上一阶段 |
|---|---:|---:|
| 原始 RTL | 42,363 | — |
| 串行化面积优化阶段，含元数据检查和地址准备 | 54,718 | +12,355 |
| 模型应答寄存及 FIFO 注册额度 | 56,214 | +1,496 |
| 共享读请求缓冲，即上一轮上板版 | 90,872 | +34,658 |
| 本轮最终实板 | 47,204 | −43,668 |

Fashion-CNN 增加请求缓冲后，激活应答等待计数由 78 增至 23,449，模型应答等待由 13,244 增至 26,488，MAC 激活等待由 10,226 增至 42,624。请求额外延迟与连续请求限制成为主要开销。逐行准备 K 组则引入每组固定的准备周期。计数器存在重叠，完整计数器应按各自含义解读。

## 最终 RTL 改动

- FPGA 共享读请求直接进入存储控制器，省去逐次访问的请求缓冲；保留模型应答寄存和输出 FIFO 注册额度。
- FPGA 模型基地址在配置变化时寄存，新请求等待一周期使基地址与配置对应。
- 命令控制器寄存配置合法性检查结果，利用命令执行间已有的握手周期更新。
- rowpad、FC 和通道数为 8 的整数倍的 K 组并行生成八行元数据，用两周期完成准备。不规则卷积保留串行路径。
- 在 K 组准备时预计算有效坐标范围，激活请求直接进行边界比较，缩短逐次请求的 padding/tap 运算路径。
- FPGA 模型读授权由寄存的轮转状态决定，每次模型读后保留激活机会，无激活请求时在下一时钟释放，降低激活 valid 对模型地址选择的组合依赖。

最终生产源码增量位于 `this_round.patch`，起点工作区增量位于 `starting_worktree.patch`。主要文件为 `npu_program_core.sv`、`npu_model_store.sv`、`npu_command_controller.sv`，以及存储器配置/公平性回归 `tb_npu_memories.sv`。

## 实现余量策略

优化阶段临时添加 0.350 ns setup user uncertainty，进行 AggressiveExplore 和 Explore 布线。验收时恢复 user uncertainty 为 0.000 ns。原始与最终 SoC 时钟周期均为 20.000 ns，自动 jitter/MMCM phase uncertainty 均为 0.084 ns；优化阶段报告为 0.434 ns。原始、收紧目标、最终报告分别保存在 `vivado_margin/original_worst.rpt`、`tightened_worst.rpt` 和 `route_timing_summary.rpt`。AMD 文档说明用户 uncertainty 会加到工具自动计算值之上，可用于收紧实现目标。[AMD UG835](https://docs.amd.com/r/2024.1-English/ug835-vivado-tcl-commands/set_clock_uncertainty)

后端脚本 `margin_from_route.tcl` 复用现有 `build_ego1.tcl` 的报告/DRC/摘要过程，提取副本为 `build_helpers.tcl`。`run_margin.py` 和最终 provenance 记录父 checkpoint、父 provenance、优化脚本和辅助脚本哈希。最终 bitstream 的频率配置为 50 MHz。

## 验证证据

- 最终 RTL 全 85 项程序回归和 19 组负向/生命周期检查通过；SRAM 宏功能模型 10 项程序回归通过。
- 坐标边界数学检查覆盖 7,311,360 组 extent/padding/tap/stride/position；模型保留区切换及持续并发模型/激活读检查通过。移除配置匹配的故障注入能检出旧基地址问题，注入文件已移除。
- Windows 未修改源文件使用 CRLF。实际构建源码逐文件下载到 `bounds_remote_source`，与本地源文件核对仅有换行差异，并用这些原始字节、SYNTHESIS/FPGA_MEM/trace-disabled 的生产定义重放七模型，逐写回数据、两次周期和全部 24 个计数器均匹配。见 `bounds_production_summary.json`。
- 同一最终路由的 CDC 复核通过。109 条 CDC-15 告警逐条匹配到 72 位请求和 37 位应答端点，未新增豁免。20 ns 限制下，请求/应答最小余量分别为 +17.776/+18.101 ns，两级同步链级间 setup/hold 通过。TAP 动态回归 100+32 组通过。
- 先通过布线与 CDC 门禁，再下载 EGo1。Vivado 配置 DONE=1、CRC 错误位为 0。七模型 14 次推理及五项板级 CDC/恢复检查全部通过。

最终板测报告为 `board_final/board_checkout_bounds_margin/report.json`，板级 CDC 为 `board_final/board_cdc_checkout_bounds_margin/report.json`，静态 CDC 为 `vivado_margin/cdc_review.json`。完整路径与源文件哈希在 provenance 中。

## 实验选择

单周期 K 组准备虽减少准备计数，权重等待随执行交错增加，七模型总周期略高，故采用两周期准备。尾块宽度寄存版与主机单项写缓冲版均通过功能回归，后者也完成 14 次模型及五项板级检查，路由 setup +0.066 ns、LUT 19,204。最终采用 +0.344 ns、LUT 18,961 的坐标预计算/余量优化版，工作区已恢复到该镜像的源码。主机写缓冲及其专项测试仅作为实验记录保存在本归档。

仅对 activation cache miss 增加一级数据寄存的探测使七模型周期额外增长约 18%–37%，因此撤回。最终源码没有引入该读取延迟。较早实验在负余量阶段停止，日志保留于远端各自构建目录。工具临时 XDC 文件失败的一轮使用相同源码换新暂存目录重跑。

## 文件与复现

最终镜像 `vivado_margin/fpga_top.bit` 的 SHA-256 为 `ef41bc0d170e4c20cfc7c8533434827fc22357d5c8a2b26627d8ebd7e2442590`。

远端归档根目录为 `D:/IWS/advanced_soc/ChipDesign/build/fpga_signoff/npu_perf_20261107`。最终构建目录为 `checkout_bounds/build/bounds_margin_50mhz`，父构建为 `checkout_bounds/build/bounds_retry_50mhz`。原始 50 MHz 源码构建使用标准 `hardware/fpga/scripts/build_nocpu.py --mhz 50 --cols 8 --implement --jobs 4`。随后使用本归档 `margin_from_route.tcl` 对父 `route.dcp` 做余量优化，完整命令在 `vivado_margin/provenance.json`。更换输出目录即可基于归档 checkpoint 重放该步骤。

本地证据包含已下载 bitstream、时序/时钟/CDC 报告、程序下载属性、每模型输入/expected/actual 二进制、源文件快照及脚本。工具历史 summary 的 five-operator 字样沿用旧标题；本轮 ABI 5.0 算子能力以源码及功能回归为准。
