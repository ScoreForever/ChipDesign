# EGo1 ABI 5.0 优化候选

- 基线 38fdd73，候选源码改动见 rtl.patch。
- 张量元数据表改用同步 RAM 逐条查找，卷积 K 组按行准备地址。
- FPGA 存储配置 RTL：85/85 程序用例通过，19 项负向和生命周期检查，七个发布模型均逐写回核对。
- 同一回归配置下，七个发布模型的片上周期较基线增加约 13% 至 29%。
- CDC 动态回归：普通 TAP 100 组、突发 32 组通过。
- Yosys 估算 16,934 LUT、4.5 BRAM36 等效，实际容量、时序和 CDC 以 Vivado 实现报告为准。

## 单端口 LUTRAM 候选

第一版双端口 BRAM 推断在 Vivado 2025.2 顶层综合中分别以 8 和 4 线程触发 EXCEPTION_ACCESS_VIOLATION。原始记录在 checkout/build/impl_50mhz 与 impl_50mhz_j4。
当前候选改为单端口 LUTRAM，状态机在不同周期读写。改动见 rtl_lutram.patch。FPGA 存储配置重新通过 85/85 程序用例及 19 项负向和生命周期检查；Yosys 估算 16,961 LUT、4.0 BRAM36 等效。Vivado 实现及上板仍待结果。

## 当前候选：独立张量查找模块

- 查找内嵌版本在默认、保留层级综合均复现工具崩溃。只保留逐行卷积地址改动的隔离探测已完成 Vivado 综合。
- 现将张量地址验证独立为 npu_tensor_lookup，保留该层级。补丁见 rtl_isolated_lookup.patch。
- FPGA 存储配置 85/85，宏功能模型 10/10。Yosys 估算 16,565 LUT。七个发布模型片上周期变化见 model_cycle_comparison.csv。
- 当前实现路径 checkout/build/impl_isolated_lookup_50mhz。只在同一候选的 50 MHz 布线后时序和 CDC 门禁均通过时下载。

## Vivado 与时序迭代

- 独立查找候选的 Vivado 综合总 Slice LUT 为 18,551 / 20,800（89.19%），较旧版综合 33,937 LUT 降约 45%。已通过布局前容量检查，完成布局。布局阶段 50 MHz setup WNS −2.118 ns、hold WHS −0.077 ns，待布线确认。
- 最差路径从共享 SRAM 的 BRAM 读出跨到 NPU 写 FIFO 的 WE。后续候选在 FPGA 配置下给模型应答增加一级寄存，已通过 85/85 FPGA 存储配置程序回归，补丁见 rtl_model_response_pipeline.patch，运行目录 checkout_pipeline/build/impl_50mhz。
- 模型应答流水版综合总 Slice LUT 18,486 / 20,800（88.88%），综合阶段 WNS −1.690 ns。最差路径转为池化地址至写 FIFO WE。
- 第三候选基于已寄存占用量发放写 FIFO 额度，去除 SRAM ready 到同周期 FIFO 入队的组合反馈。RTL 回归 85/85，补丁见 rtl_fifo_credit.patch，远端运行 checkout_credit/build/impl_50mhz。该候选的七模型周期变化见 credit_model_cycle_comparison.csv。

## 共享读请求流水候选

- FIFO 额度版 Vivado 综合 18,692 LUT，综合阶段最差路径转到 model_bytes → 共享读地址与 ready → model_reads CE。
- 当前工作区增加 FPGA 读请求缓冲，寄存选定地址和模型/激活来源标签。FPGA 存储配置 85/85；Yosys 估算 16,265 LUT。补丁见 rtl_request_pipeline.patch。
- 远端工作区 checkout_req_pipeline，构建输出 checkout_req_pipeline/build/impl_req_50mhz。构建状态须以该目录 summary.md 和布线报告为准。

## 已完成的布线后结果

独立查找版布线后为 18,516 / 20,800 LUT（89.02%）。50 MHz WNS −1.694 ns、TNS −1116.673 ns、WHS +0.043 ns，验收失败。CDC 端点复核通过，109 条警告逐条匹配到 72/37 位，20 ns 限制下请求/应答最小余量 +18.066/+18.644 ns。bitstream 已生成，但未下载。

## 最终候选的 50 MHz 布线后验收

- 基线 38fdd73，候选增量见 `rtl_request_pipeline.patch`。维持 ABI 5.0、8×8 阵列和 128 条描述符上限。
- 50 MHz 时序通过。WNS +0.014 ns、WHS +0.040 ns，TNS/THS 均为 0，setup/hold 失败端点均为 0。布线后使用 18,196 / 20,800 Slice LUT（87.48%）。
- CDC 复核通过。72 位请求和 37 位应答在 20 ns 限制下的最小余量分别为 +18.098 ns、+18.601 ns。两级同步链的级间时序通过。109 条原始 CDC-15 告警均与预期端点对应，未新增豁免。TAP 动态回归 100 + 32 组通过。
- FPGA 存储配置程序回归 85/85，宏功能模型回归 10/10。七个发布模型的仿真片上周期相对基线增加 63.32% 至 114.51%，详见 `request_pipeline_model_cycle_comparison.csv`。setup 余量仅 14 ps。
- 归档目录 `vivado_req_pipeline` 包含最终实现报告、源文件哈希和 CDC 复核结果。

## EGo1 下载与板测

- 通过 Vivado 下载到 `xc7a35t_0`，DONE=1，配置 CRC 错误位为 0。镜像 SHA-256 为 `067420299385c5be2094d4a3c25df3fc5a051f3c858bcc149d21647748eeb034`。
- NPU 系统时钟配置为 50 MHz，外置 FT232H 的 OpenOCD TCK 设置为 1 MHz。INT4 模型连续运行 3 次，均逐字节匹配软件参考输出 `[-68, -34, -11]`。
- 板级 CDC 复测 5/5。运行中暂停 JTAG 扫描约 1 秒，恢复后任务完成且输出匹配。运行中重复 START 返回 4，协议 RESET 能取消任务，watchdog 返回 10 并可复位恢复，恢复后重新推理输出匹配。
- 初次测试负载在 busy 状态读取和第二次 START 之间结束，返回结果待取 9。将 Conv/Requant 从 16 对增至 56 对后，同一镜像完成全部检查。初测和复测记录分别保存在 `board/cdc_first`、`board/cdc`。
- 本轮范围为 50 MHz 实现、JTAG CDC 与功能冒烟。物理复位按键及电气波形待单独验证。
