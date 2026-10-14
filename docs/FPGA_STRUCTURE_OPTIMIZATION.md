# FPGA 结构优化与验证（2026-10-14）

本文记录资源优化完成时的结果。后续配置/BRAM DRC 修正、SRAM 读延迟变化
及最新资源结果见 [`FPGA_DRC_CLOSURE.md`](FPGA_DRC_CLOSURE.md)。

当前 `main`（基线提交 `109513f`）的 `fpga_top` 在 `xc7a35tcsg324-1`
上原有 LUT 超容量问题。本轮调整激活存储容量并简化重定量舍入电路，保持
现有计算流程和握手周期。Vivado 2025.2 综合及 `opt_design` 均成功完成；
优化后 LUT 占用从 102.13% 降到 93.39%。

## 结构变化

### 按张量生命周期分配激活存储

`tinycnn8_npu_top` 增加独立的 `ACT_A_BANK_BYTES`、`ACT_B_BANK_BYTES`
参数，默认均继承原有 `ACT_BANK_BYTES`，保留独立核的原有容量配置。
生产 MMIO wrapper 采用 A=640 字节、B=2560 字节：

| 存储区 | 存放的张量 | 最大容量 |
|---|---|---:|
| A | 输入 320 B、Pool1 640 B、Pool2 160 B | 640 B |
| B | Conv1 2560 B、Conv2 640 B、GAP 8 B | 2560 B |

总声明容量由 8192 B 降为 3200 B，减少 60.94%。两区继续使用八个分银行，
维持异步读取、同步写入和原有数据顺序；读写地址宽度分别按各区深度计算。

### 缩小 Q31 舍入电路

`requant_unit` 原来执行 64 位符号相关 nudge、加法及取负，再截取高乘积。
对合法的 INT32×INT32 乘积，原有两种符号规则都等价于
`floor((product + 2^30) / 2^31)`。保留结果字加上 `product[30]` 即可，
因此改成 `product[62:31] + product[30]` 的 32 位加法。
INT32_MIN×INT32_MIN 的饱和特例仍返回 INT32_MAX；流水线相位和握手未改变。

## Vivado 结果

顶层 `fpga_top`，器件 `xc7a35tcsg324-1`，新建 Clocking Wizard 将
100 MHz 板载时钟转为 50 MHz。引脚约束读取本机 Lab6 的 `fpga_top.xdc`。
基线与最终版本均执行相同的综合和网表优化。

| 指标 | 原有版本（opt_design 后） | 本轮版本（opt_design 后） |
|---|---:|---:|
| LUT | 21,243 / 20,800（102.13%） | 19,425 / 20,800（93.39%） |
| FF | 14,023 | 14,008 |
| BRAM36 | 2 | 2 |
| DSP | 51 | 51 |
| WNS（综合网表估计） | +0.341 ns | +1.841 ns |
| TNS | 0 | 0 |

LUT 减少 1818 个（8.56%），当前网表有 1375 个 LUT 容量余量。
最终综合阶段报告 418 个 Warning、0 Critical Warning、0 Error；
综合及后续网表优化的 Vivado 进程均以 0 退出。

尚未布局布线或生成 bitstream，以上时序为综合网表估计。JTAG 10 MHz
及其与系统时钟异步的关系是验证脚本的假定；板级输入/输出延迟仍有缺项。
现有配置电压、DSP 流水线、BRAM 输出寄存器和 BRAM 异步控制警告仍需在
实现阶段处理，`drc_opt.rpt` 记录了 71 项 Warning。

## ModelSim 回归

新增可重复执行的入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_structure_regression.ps1
```

脚本创建独立的 ModelSim 库，检查退出状态、明确 PASS 标记和 Fatal/Error，
并关闭 VCD 记录以避免生成大型波形。最终脚本已在 ModelSim 2019.2 实际运行：

| 测试 | 结果 |
|---|---|
| Q31 完整 INT32 舍入结果 | 144 组有符号边界乘积、20,000 组随机乘积通过独立除法参考模型 |
| 重定量握手与输出 | 93 次输入/输出、32 次背压停顿；保留 -32 回退和掩码检查 |
| 非对称激活存储 | 6/4/8 类连续推理及非均匀空间输入通过；覆盖 A 的 639、B 的 2559 最高地址 |
| 原有 4096/4096 容量 | 相同四组非零网络通过，同样为每次 71,302 周期 |
| CPU→MMIO→NPU 集成测试 | PASS，100,728 周期（全零输入/模型 smoke） |
| DMA | PASS，725 周期 |

原有重定量测试台假定输入接受后单周期输出，在未优化 RTL 上也会报
`registered valid mismatch`。本轮把计分板和 drain 改为等待实际输出握手，
并保留独立算术参考模型，增加截断到 INT8 之前的完整 INT32 比较。

非零网络测试直接加载当前核的 Conv/Pool/Conv/Pool/GAP/FC 参数，
使用中心单位卷积、逐通道 bias、两级池化、GAP 和有符号 FC 权重，
以独立计算的 logits 校验输出。当前 RTL 的 GAP 路径与仓库部分 Flat
文档/历史测试的网络描述不同；本轮保持当前实际 RTL 架构，新增测试专门
覆盖它，未宣称 Flat/训练模型的全部历史回归通过。

## 本机证据

- 基线报告：`work/vivado_verify_20261014/`。
- 最终综合、资源、时序、DRC、DCP 和 Tcl：`work/vivado_structure_final_20261014/`。
- 最终完整仿真日志：`work/structure_regression_20261014_123443/`。

在最终 Vivado 证据目录中可依次运行 `run_synth.tcl` 和 `run_opt.tcl`；
它们依赖本机 Vivado、当前仓库源码和上述 Lab6 引脚约束。生成产物位于
Git 忽略的 `work/`；本轮代码与测试改动尚未提交或推送。
