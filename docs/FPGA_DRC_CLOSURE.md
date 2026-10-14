# FPGA DRC 修正与自动检查（2026-10-14）

> 历史配置说明：本文资源/周期/DRC证据属于7569a43的GAP配置。PR #5合并后默认
> 核为融合Flat，保留SRAM/AXI和约束修复，但须重新综合验证，不能沿用这些PPA数字。
> 原GAP容量测试现已适配为`tb_tinycnn8_capacity`。

本轮完成配置电压和 BRAM 控制/输出寄存器修正，并修复自动检查脚本的
Vivado 2025.2 接口兼容问题。当前综合网表的配置/BRAM 专项检查为零违例；
完整 DRC 仍显示 47 项 DSP 流水线性能建议，未屏蔽、降级或创建 waiver。

## DRC 状态

| 检查 | 修正前报告展示数 | 修正后 | 处理 |
|---|---:|---:|---|
| CFGBVS-1 | 1 | 0 | 补齐 EGo1 配置电压约束 |
| REQP-1839 | 20（达到报告上限） | 0 | BRAM 前增加无异步复位的同步地址、使能和数据寄存器 |
| RBOR-1 | 2 | 0 | BRAM 后增加同步输出级，两块 RAM 均已启用 DOA_REG=1 |
| CHECK-3 | 1 | 0 | REQP-1839 违例消失，报告不再截断 |
| DPIP-1 | 14 | 14 | DSP 输入流水线性能建议保留 |
| DPOP-1 | 12 | 12 | DSP PREG 性能建议保留 |
| DPOP-2 | 21 | 21 | DSP MREG 性能建议保留 |

修正前 71 项与修正后 47 项是报告展示数；修正前 REQP-1839 被限制为
20 项，不能据此推断原有实际 BRAM 风险违例的总数。

剩余建议中 16 项位于 CPU、31 项位于 NPU。CPU 乘法器和 NPU MAC 的
寄存器级数与指令/握手、阵列节拍绑定，增加 DSP 内部级数需要同步调整
控制和延迟。这些建议保持可见，尚未进行该流水线改造。综合网表满足
当前 50 MHz 约束，不代表布局布线之后也满足时序。

## 配置与 SRAM 修改

`hardware/fpga/ego1_configuration.xdc` 设置 `CFGBVS=VCCO`、
`CONFIG_VOLTAGE=3.3`，只适用于本项目的 EGo1 `xc7a35tcsg324-1` 目标。
EGo1 手册确认该器件、100 MHz 输入及 3.3 V SPI 配置接口：
[EGo1 用户手册](https://e-elements.readthedocs.io/zh/ego1_v2.2/EGo1.html)。
该 XDC 已由本轮独立综合工程读取；在其他 Vivado 工程复现时需加入其约束集。

`sram_ff` 增加可选 `PIPELINED` 模式，默认仍为原有单拍模式。生产 SoC
开启流水线模式，使用请求输入级、RAM 读取级和同步输出级，读延迟为三拍。
BRAM 控制寄存器没有异步复位；复位在输入采样边沿屏蔽新请求，已经接受的
请求可以完成，SRAM 内容在复位时保留。AXI 适配器增加 `READ_LATENCY`
参数及等待状态，直到对应数据稳定才输出 RVALID，背压期间不发起下一拍。
其他 AXI 从设备仍使用默认一拍配置。

## 自动 DRC 检查

在已打开的综合/优化设计中执行：

```tcl
source hardware/fpga/check_bram_drc.tcl
```

脚本要求 CFGBVS-1、REQP-1839、RBOR-1 三项检查都可用，生成名为
`chipdesign_bram_guard` 的专用报告，再通过
`get_drc_violations -name chipdesign_bram_guard` 查询。存在任何专项违例
就抛出 Tcl 错误。该查询限定到专用报告，避免其他完整报告中的 DSP 建议
干扰检查；完整 DRC 应继续另外运行。

原来的 `get_drc_violations -of_objects` 不受本机 Vivado 2025.2 支持，
已根据本机 `get_drc_violations -help` 修正。两项实际 Vivado 验证均完成：

- 当前优化网表：专项检查零违例，打印 PASS，进程退出码 0。
- 修正前网表：脚本因配置/BRAM 违例抛出预期错误，负向测试捕获并确认错误，
  打印原网表被拒绝的 PASS，测试进程退出码 0。

正向运行中的 `No violations matched '*'` 是零违例查询提示，不是新增
DRC 违例。脚本保留 API 错误，不使用 `-quiet` 吞掉接口异常。

## 验证结果与代价

最终 ModelSim 回归入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_structure_regression.ps1
```

在本机 ModelSim 2019.2 的完整执行通过：

- 原有重定量和两种激活容量的非零推理测试全部通过，核内每次推理仍为
  71,302 周期。
- SoC 全零模型 smoke 为 109,141 周期，原来为 100,728 周期。
- DMA smoke 为 917 周期，原来为 725 周期。
- 新增 AXI/SRAM 测试在一拍和三拍两种模式均通过：各 18 次写、32 次读，
  覆盖 8 拍 INCR 突发、AW 先于 W、写入间隔、字节使能、零掩码写、
  R/B 通道背压、最高 SRAM 地址及挂起读期间复位后重读。

读流水线改善了 BRAM 控制和输出寄存器结构，同时增加 CPU/DMA 读等待，
集成测试的周期代价如上；没有把新的周期数解释为核内推理性能退化。

Vivado 2025.2 `synth_design`、`opt_design` 和随后重新打开 DCP 的 DRC 审计
均成功。当前优化后资源：LUT 19,368 / 20,800（93.12%）、FF 14,057、
BRAM36 2、DSP 51。50 MHz 综合时序估计 WNS +1.841 ns、TNS 0。
尚未布局布线，板级 I/O 延迟仍需补齐，未生成 bitstream。

初次本轮综合控制台出现过一次 `Common 17-354: Could not open 'C' for writing`，
随后综合明确完成、进程以 0 退出并生成 DCP；独立网表优化及重新打开 DCP
的审计都成功。该工具日志异常保留在原始控制台日志中。

## 本机证据

- 综合、优化、完整 DRC、专项正反测试和 DCP：`work/vivado_drc_fix_20261014/`。
- 专项正向测试：`audit_console.log`；反向测试：`audit_negative_console.log`。
- 未屏蔽 DSP 建议的完整审计：`drc_audited.rpt`。
- 最新完整 ModelSim 回归：`work/structure_regression_20261014_124642/`。

对应改动已包含在当前main（7569a43）。前一轮资源优化的历史结果见
[`FPGA_STRUCTURE_OPTIMIZATION.md`](FPGA_STRUCTURE_OPTIMIZATION.md)。
