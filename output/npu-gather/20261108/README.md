# 非对齐激活取数 RTL 优化与实板验收

最终选用分片 gather、扫描步进预计算和并行写转发版本。ABI 5.0、8×8 阵列、INT4 数值格式、128 描述符上限与主机模型包格式保持兼容。当前工作区所有构建输入已与最终镜像哈希核对，先通过布线和同镜像 CDC 门禁，再下载 EGo1。代码未提交或推送。

## 验收结论

标准 50 MHz 完整流程的首次 route_design 即通过，没有触发负余量的 AggressiveExplore/Explore 重试分支。console.log 中仅有一次常规 phys_opt_design 和一次 route_design；记录见 implementation_flow.json。未使用归档的额外 setup uncertainty 调优脚本生成本轮最终镜像。

| 项目 | 本轮起点已上板镜像 | 最终新镜像 |
|---|---:|---:|
| 系统时钟 | 50 MHz | 50 MHz |
| 路由 WNS | +0.344 ns | +0.031 ns |
| 路由 WHS | +0.058 ns | +0.034 ns |
| TNS / THS | 0 / 0 | 0 / 0 |
| LUT | 18,961 / 20,800 | 18,464 / 20,800（88.77%） |
| FF | 7,242 | 7,263 |
| DSP | 16 | 12 |
| BRAM tile | 4 | 4 |

新 RTL 满足 50 MHz 时序门禁，首轮路由闭合，性能与面积有所改善；setup/hold 余量仍小于起点镜像，不能宣称余量已优于上一轮。FPGA 资源为 xc7a35tcsg324-1。

## 七模型芯内推理周期

每模型使用发布 golden 的第一帧，各执行两次。实板逐字节输出、周期及全部 24 项性能计数均与生产配置 RTL 一致；这不是整套数据集精度测试。周期只覆盖片上推理，不包含 JTAG 装载与主机通信时间。

| 模型 | 起点实板周期 | 新版实板周期 | 变化 |
|---|---:|---:|---:|
| ecg | 15,857 | 15,854 | -0.019% |
| fashion_cnn | 47,204 | 47,176 | -0.059% |
| gtsrb_l | 128,283 | 95,147 | -25.830% |
| har | 18,836 | 18,836 | +0.000% |
| kws_l | 86,626 | 86,536 | -0.104% |
| kws_m | 88,821 | 88,754 | -0.075% |
| mnist_l | 37,086 | 37,059 | -0.073% |

GTSRB-L 降低 25.83%，其余六模型无周期回退。小型 8×8 输入、4 通道、3×3 卷积用例为 1550→1027 周期（−33.7%）、582→330 次激活请求（−43.3%）、710→187 周期激活等待（−73.7%）。固定回归预算为≤1050周期及≤340读请求，起点实现无法满足。

## RTL 原理与改动

1. **按连续子集 gather**：旧实现只有全组连续时合并读取，其他组逐 lane 请求。新实现复用 K 组地址关系矩阵，每次读覆盖一个连续子集，合并到向量并清除已完成 mask，继续读取剩余首 lane；padding 保持零值，最后片段仍可同周期返回并送入 MAC。4 通道 3×3 跨 kernel 行的一组 8 个数通常为两段各4个数，可从8次标量请求减到2次读取。
2. **配置阶段预计算扫描步进**：INIT_B 生成普通扫描、1×2 和 2×2 池化的8项 cursor_step。运行时按相位/行尾选择寄存增量，执行一次基地址加法，移走 +y_step−x_step 等串联加减链，不增加配置或推理周期。
3. **并行写转发**：物理队列项并行比较地址；逐 nibble 按 head 划分回绕区间，在有效匹配中选最新值，再用 one-hot 合并数据。去掉按逻辑年龄动态索引整组队列数据及8项串行 overlay。保持8项队列、最新写优先、同沿读前写后、held snapshot、RMW 和响应延迟。

gather_scalar_groups 保留 ABI 5.0 的历史字段名：计数的是需要分片读取的非连续组，不是标量读取次数；读取次数查看 activation_requests。

GTSRB 首物理组（Conv/Requant/MaxPool）由75,697降到42,561周期，其中激活等待51,457→18,321，MAC发射次数保持15,360。最终最差路径为 cursor_at_last_x→激活存储 held_data，22级逻辑、4个CARRY4，数据延迟19.889ns，其中布线15.068ns（75.8%）。早期仅gather版最差路径为25级、8个CARRY4。剩余余量主要受请求地址/控制到存储返回通路及布线距离限制，后续前瞻地址流水须同时保留请求吞吐，避免重新引入逐次串行请求等待。

## 功能、CDC 与板测覆盖

- 81项 FPGA 存储配置程序回归，另19组非法输入/取消/超时/生命周期检查通过；完整回归还检查存储RMW、掩码/容量回绕、held snapshot、500次随机访问和压力行为。
- 15项专项覆盖2/3/4/5/7/9通道、有符号/无符号、非对称padding、stride、尾通道，以及单窗口1×2/2×2池化行尾。
- 环形写转发18432组 head/count/match组合检查，覆盖两目标字、容量回绕与每个nibble；旧/新逻辑均通过独立顺序参考。破坏回绕优先级的故障注入在head1/count8被检出，注入源已删除，日志保留。新testbench已接入run_program_regression.unit_tests。
- 端到端传输回归8项（4项直接对照、4项JTAG）；原生存储路径18项通过。七模型在 SYNTHESIS/FPGA_MEM、trace全关闭的生产定义下重放通过，全部写回、周期和24项计数一致。
- 同一路由 CDC：100+32动态TAP用例通过；109条CDC-15告警逐条对应72位请求和37位应答，无新增豁免或未复核告警。20ns限制下请求/应答最小余量+17.402/+17.177ns，两级同步链级间setup/hold通过。
- 门禁通过后编程 EGo1：DONE=1、CRC错误位=0。TCK配置1MHz，七模型14次输出、周期及24项计数全部匹配。5项板级检查为暂停TCK、busy重复START、busy协议RESET、watchdog、恢复后推理。

实体复位按键与电气波形仍属人工检查项；数字仿真不证明模拟亚稳态行为。本轮 FPGA 验收不代替 ASIC 流片签核。首次OpenOCD因WSL USB设备不可见而未启动推理；恢复指定FT232H序列号与plugdev访问后完整重跑通过，失败连接日志保留。

## 文件与复现

最终 bitstream：vivado_forward/fpga_top.bit。SHA-256：`535c93afe24ab10f02b424a4a8a9d6918e01d9c56165572fb84e6b82336d6350`。

远端：D:/IWS/advanced_soc/ChipDesign/build/fpga_signoff/npu_gather_20261108/checkout_forward/build/gather_forward_50mhz。工作区源文件、冻结输入、实现provenance及生产RTL摘要已逐文件哈希核对，板测也校验同一bitstream。

```sh
python3 hardware/npu/scripts/run_gather_regression.py --output build/gather_edges
python3 hardware/npu/scripts/run_program_regression.py --quick --packaged-models --wave-disabled --define SYNTHESIS --define CHIPDESIGN_FPGA_MEM --jobs 4 --output build/gather_full
python hardware/fpga/scripts/build_nocpu.py --vivado-root D:/AMDDesignTools/2025.2/Vivado --output build/gather_50mhz --mhz 50 --cols 8 --implement --jobs 4
python3 verification/fpga/cdc/run_npu_jtag_cdc.py --static-evidence build/gather_50mhz --output build/gather_cdc
```

构建输出必须是新目录。源码增量见this_round.patch（生产RTL、测试及入口/说明），rtl.patch仅含两个RTL文件。source冻结清单forward_sources_sha256.json；forward_*.json为最终版本回归；model_cycles.csv和performance_counters.csv为新实板测量；board_counter_comparison.json核对24计数。最终路由/CDC/编程记录在vivado_forward、cdc_forward、program_gate.json，实板原始输入/expected/actual及报告在board_final。

实验轨迹：仅gather版路由0.000/+0.059、步进表版+.014/+.039作为对照保留。gather_last寄存候选及额外后端调优均停止，未并入最终源码。一次前置归档失败后误启动未覆盖源码的构建已停止并排除；正式候选通过build_forward_frozen.py校验20个输入后启动。步骤版、早期候选数据以对应前缀区分，不能作为最终签核。
