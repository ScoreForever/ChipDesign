# PicoRV32 CPU 替换与验证

日期：2026-10-17。分支：`exp/smaller_cpu_PicoRV32`。
对照基线：`9d25b0965ba4c190c4ec352d4f4b9b5b5920273b`。

已删除 `hardware/cpu/cpu_cv32e40p/`（含原有未使用的 FPU vendor 源码）和旧专用 clock gate，
接入固定提交的官方 PicoRV32。未修改 NPU 运算、MMIO 数据 ABI 或训练权重。
本次完成仿真和 SoC 综合；未布局布线、生成/烧写 bitstream 或连接真实 JTAG 调试器。

## 实现

- 官方源码：`hardware/cpu/picorv32/picorv32.v`；提交
  `ef203c2b0a3fb793280f5114941416c425c5b461`。原文件和许可证完整保留，
  `UPSTREAM.json` 记录 Git blob 和 SHA256，已与下载的官方 Contents API 元数据核对。
- 配置为 RV32IMC、32 个整数寄存器、单读口寄存器堆、迭代乘除法、64 位周期/指令计数器、
  Pico 自定义中断；关闭快速乘法器和 IRQ timer。保留 M/C 是为了运行当前 C 固件与量化后处理。
- `picorv32_soc.sv` 将统一 native valid/ready 访存接入 AXI。独立处理 AW/W、保持字节使能、
  等待 B/R 完成后才应答 CPU；非 OKAY 响应锁存总线故障。CPU 复位时桥继续排空已接受事务。
- CPU 原取指/数据两主端口合为一个，交叉开关变为 CPU、调试 SBA、DMA 三主四从。
  BootRAM 仍跳到 `0x80000000`；SRAM 仍为 8 KiB；NPU 与 DMA 地址不变。
- 固件移除 `mcountinhibit` 写入，计时由 `mcycle`（0xb00）改读 `cycle`（0xc00）。
  构建器使用英文临时目录绕过 Windows GCC LTO 的中文路径问题，禁用可能占用镜像首地址的 build-id note，
  并支持 `--library-dir` 指定本机 Vivado GCC 的目标 multilib。
- 更新 filelist、当前架构说明、访存探针及分析器。分析器继续兼容历史双端口日志，
  新日志 schema=2 表示统一 CPU 主端口。

## 调试和启动兼容性

**PicoRV32 没有原 CPU 的 RISC-V Debug Mode。** 本次保留 JTAG DTM 和 Debug Module 的
系统总线访问，但不提供原有 hart halt/resume、寄存器抽象访问、GDB 单步，也不声称旧 OpenOCD
hart 配置可以原样使用。Debug Module 将 hart 报为 unavailable，避免伪装调试支持。

新增两个原先未使用的 BootRAM 字：

| 地址 | 语义 |
| --- | --- |
| `0x00010038` | 只读：bit0 stopped（CPU 复位且桥已排空），bit1 trap，bit2 请求运行 |
| `0x0001003c` | bit0 run：0 复位 CPU，1 从 BootRAM 重新执行 |

FPGA 顶层设置 `CPU_BOOT_HOLD=1`，配置后等待程序装载。仿真 SoC 默认不 hold，继续支持原有
`$readmemh` 预载程序。重新启动不是恢复先前 PC；CPU 单独复位保留 SRAM/NPU/DMA，主机应等
DMA/NPU 空闲再覆盖主存。

`software/tools/picorv32_sba.tcl` 提供不依赖 hart examine 的原始 DMI/SBA 装载助手：

```tcl
# 先用板卡自己的 OpenOCD 配置建立 adapter/TAP；这里不假定 TAP 名称或引脚。
source software/tools/picorv32_sba.tcl
pico_sba_init <已配置的TAP名称>
pico_load_hex <workload.hex路径>
```

助手先复位 CPU、等引擎空闲，装入并完整读回校验程序，再释放 CPU；校验失败或引擎不空闲时
CPU 保持复位。物理 JTAG TAP/适配器仍待板上验证；仿真覆盖从 DMI 边界开始的真实 DM/SBA/AXI 通路。

中断号仍为 NPU=16、DMA=17，但入口固定为 `0x80000010`，处理代码使用 Pico 的
`maskirq/getq/setq/retirq`，不能沿用 `mtvec/mstatus/mret`。现有 C 运行时继续轮询，不启用中断。

## 同条件综合

Vivado 2025.2、器件 `xc7a35tcsg324-1`、顶层 `my_soc_top`、`CHIPDESIGN_FPGA_MEM`。
两版均使用相同默认综合设置，综合后设置 20 ns 时钟用于诊断；不是带完整板级约束的时序驱动实现。
不包含 `fpga_top` 的 MMCM/LED，不与演示图中的布线后数值混作一个基线。

| 项目 | CV32E40P 基线 | PicoRV32 | 变化 |
| --- | ---: | ---: | ---: |
| CPU（新核包含适配桥）LUT | 4,532 | 1,998 | −55.9% |
| AXI 交叉开关 LUT | 3,047 | 2,656 | −12.8% |
| SoC 总 LUT | 14,423 | 11,156 | −3,267 / −22.7% |
| SoC FF | 11,210 | 9,975 | −1,235 / −11.0% |
| DSP | 41 | 36 | −5 |
| BRAM36 | 2 | 2 | 不变 |

两次综合均成功；新版本 0 Critical Warning、0 Error。以上资源为综合结果，不代表 routed timing、
板上功耗或最终布线后 LUT。CPU 缩小也不意味着端到端延迟变短。

复现：

```powershell
python hardware/cpu/picorv32/measure_area.py --vivado <Vivado路径>/bin/vivado.bat `
  --baseline-ref 9d25b0965ba4c190c4ec352d4f4b9b5b5920273b `
  --output work/picorv32_area_reproduce
```

原始证据：`work/picorv32_area_compare/{baseline,picorv32}/` 的 `utilization.rpt`、
`hierarchical.rpt`、`console.log`、`synth.dcp` 和 Tcl；该目录按项目惯例不入 Git。

## 功能验证

ModelSim 2019.2 在 ASIC 行为 SRAM 与 FPGA 三拍 SRAM 两种配置上通过：

| 验证项 | 结果 |
| --- | --- |
| 原 CPU→MMIO→NPU 全零 smoke | 两种配置通过 |
| 原 DMA 集成与最后写响应屏障 | 两种配置通过 |
| RV32IMC/计数器 | 有符号及无符号乘除、除零/溢出、SB/SH/LB/LBU/LH/LHU、跨 word 压缩指令取指通过 |
| IRQ16/17 | 注入外设 IRQ，实际执行 Pico 处理程序与返回，通过 |
| DMI SBA | 通过真实 DM/AXI 装载、读回程序、启动、停止、重装和 trap 恢复，通过；绕过物理 TAP |
| AXI 异常 | 未映射地址读/写返回错误，CPU 锁存故障，复位后恢复，通过 |
| 原 requant/Flat 容量/SRAM 背压回归 | 两种配置通过 |
| C 运行时 | CPU-MMIO 与 DMA/pipeline 两种固件、两种存储配置均逐 raw logit 和最终类别匹配 Python golden |
| 访存分析 | FPGA CPU-MMIO 工作负载匹配 8,937 笔 native/AXI 事务；分析器历史/新 schema 的 12 个构造日志检查通过 |
| 主机装载助手 | Tcl 语法与 4 个模拟 SBA 行为测试通过；未连接物理 JTAG |

C 测试使用仓库训练部署包 `software/models/tinycnn8_flatten_strong.npz`，输入是 NumPy
`default_rng(17).integers(-128,128,(3,320),dtype=int8)` 的三帧确定性测试数据。
这是部署链路逐位验证，不是新一轮真实音频准确率测试。四个配置合计 12 帧、48 个 raw logit 比较。

FPGA 三拍主存配置下，本次测试的实测周期：

| 模式 | 输入阶段 | start/轮询完成阶段 | 结果读取及分类 | 核内 PERF_TOTAL | 稳态启动间隔 |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU 逐字节 MMIO | 15,262 | 3,057 | 2,748 | 2,805 | 21,679 |
| DMA + pipeline（后续帧） | 4,124 | 3,075 | 2,629–2,660 | 2,805 | 7,420 |

DMA 输入计时包含流水中提交下一帧的工作，阶段存在重叠，不应简单相加或当作孤立单帧延迟。
50 MHz 下 7,420 周期对应 148.4 µs 稳态启动间隔。没有做同条件旧 CPU 的 C 工作负载测速，
因此不据此声明相对旧 CPU 的加速/减速比例；NPU 核心仍为 2,805 周期。

复现现有已构建工作负载：

```powershell
python hardware/soc/sim/sw/gen_picorv32_test.py
python hardware/soc/sim/scripts/run_picorv32_regression.py --config both `
  --workload build/picorv32_cpu --workload build/picorv32_dma
python software/tests/test_picorv32_sba.py
python software/tools/selftest_kws_access_profile.py
```

构建工作负载仍使用 `software/tools/build_kws_workload.py`，CPU 模式加 `--use-dma 0 --pipeline 0`；
DMA 模式使用默认设置。通过 `--tool-prefix` 指向本机 RISC-V GCC；重新编译后再运行上述命令。

本机证据：`work/picorv32_verified/`（16 项主回归）、`work/picorv32_fault_verified/`
（补充总线故障恢复）、`work/picorv32_profile_final/`（实际访存），各有日志与 `results.json`。
