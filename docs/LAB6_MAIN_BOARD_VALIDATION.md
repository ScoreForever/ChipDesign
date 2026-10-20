# main 分支 EGo1 构建与板上验证

日期：2026-10-20。当前结论：**比特流生成、烧写、CPU JTAG 和 GDB 验证全部通过。**

后续扩展验证已加入非零模型、DMA、量化边界、CPU IRQ、整片SRAM、复位及RTL比对，见[验证工作流](../verification/fpga/README.md)和[最新结果](../verification/fpga/results/2026-10-20/report.md)。
上述“通过”描述构建和连通性；**不表示完整RTL功能/实现签核**。扩展审计发现JTAG复位跨域和CDC/IO约束问题，最终报告单独列出阻塞项。

## 最终可用配置：TCK 改接 J5 第21脚

对本机板卡，TCK 改接 `J5 第21脚 / H16`，TDI 恢复第18脚；其余接线不变。
第17脚留空。`hardware/fpga/ego1_pins.xdc` 已更新，当前 main 有这处本地约束修改，未提交。

| 下载器信号 | J5脚号 | FPGA管脚 |
|---|---:|---|
| TCK | **21** | **H16** |
| TDI | 18 | E16 |
| TDO | 19 | D15 |
| TMS | 20 | C15 |
| GND | 33或34 | GND |
| VREF | 35或36 | 3.3V |

最终比特流：`build/lab6_main_h16_20261020/fpga_top.bit`。
使用 main `9d25b09` 的已验证 opt.dcp，仅更改管脚约束后重新布局布线；不修改 CPU/NPU RTL。
外部 TCK 允许非专用时钟布线。系统50 MHz，最终 WNS `+1.053 ns`、WHS `+0.009 ns`，
现有约束全部满足；主 SRAM 仍为2块RAMB36，DRC无Error或Critical Warning，普通建议保留。
烧写后 DONE=1，CRC_ERROR=0。比特流 SHA-256：

```text
015e93fa87080f09380b687ffbdc8c5923483fc7e443555280c049a12e02a316
```

CPU JTAG 实测 TAP IDCODE `0x00000001`，`datacount=2`、`progbufsize=8`、
`XLEN=32`、`misa=0x40001104`，CPU可halt，GDB监听3333。

GDB实测：

- BootRAM前三个字为 `800002b7 00028293 00028067`。
- SRAM写入读回通过；设置`t0=41`，在SRAM执行`addi t0,t0,1`单步后`t0=42`。
- 204字节测试程序加载后读回SHA-256与原始binary完全一致：`35da55817a83d8c71e18404ff169db85910774107aa740f24604d6a7a06774f2`。
- 运行CPU→NPU零模型TinyCNN测试后，`0x80001fe0 = 0xc0dec0de`，四个输出均为0，`LAB6_CPU_NPU_SMOKE=PASS`。
- 最终 `LAB6_GDB_CHECK=PASS`，检查完成时CPU保持暂停，OpenOCD保留运行。

最终证据位于 `build/lab6_main_h16_20261020/`：`summary.md`、`program_board.log`、
`programmed_board_properties.txt`、`openocd.log`、`gdb_verify.log`及实现报告。
固件读回文件为原构建目录的 `firmware_readback.bin`。

继续调试：

```powershell
./scripts/lab6/lab6.ps1 -Action Gdb
```

重新上电后需重新烧写最终H16比特流，程序也需重新通过GDB装载。
重新启动OpenOCD前先退出已有实例，避免USB/端口占用。
以下记录保留原E15配置与定位经过，不能作为最终接线使用。

## 已完成

- 源分支 `main`，提交 `9d25b09`；构建时受跟踪源码无本地修改。
- Vivado 2025.2，器件 `xc7a35tcsg324-1`，系统时钟 50 MHz。
- FPGA 配置的 ModelSim 结构回归全部通过：重定量、容量、完整 SoC、DMA、1/3 拍 SRAM。
- 综合、布局、布线和 bitstream 生成通过。
- 布线后 WNS `+0.712 ns`、TNS `0.000 ns`、WHS `+0.015 ns`。
- LUT `13,879 / 20,800`（66.73%），FF `11,255 / 41,600`，BRAM36 2，DSP 41。
- 配置/BRAM 专项 DRC 无违例；完整 DRC 无 Error / Critical Warning。
- 通过板载下载线 `1234-tulA` 烧写 `xc7a35t_0`；DONE 内部信号和管脚均为 1，CRC_ERROR 为 0。

比特流：`build/lab6_main_20261020/fpga_top.bit`。
SHA-256：

```text
e43807f24caede4218a15e404b0d7fb0d25dbb3eb3e3dc0953b3e12fd997cc1b
```

此文件烧写 FPGA 的易失配置，未写配置 Flash。重新断电后需重新烧写。

## 原E15配置的 JTAG 定位（历史）

外部 FT232H `0403:6014`，序列号 `210512180081`，BUSID `1-3`，已转发到 Ubuntu WSL。
USB 节点的 `plugdev` 权限正常，OpenOCD 可以打开下载器。

烧写后 OpenOCD 在 10 kHz 下扫描得到 `all zeroes`、IR capture `0x00`，没有识别出 CPU TAP。
这不是有效的 CPU 连接；短暂出现的 GDB 监听端口也不代表目标已被识别。
用户确认 K3 锁定灯亮、M1 心跳灯闪烁；连续发送 262,144 个 TCK 周期时，用户观察 K1 活动灯不变。
FT232H 内部 64 字节 MPSSE 回环通过，说明 USB/MPSSE 收发可用；它不验证外部引脚电气状态。
因此优先核对外部 TCK、VREF、GND 及 J5 针脚方向，尚不能声称 CPU JTAG 或 GDB 已连通。

## 验证脚本与过程

连接修正后启动：

```powershell
./scripts/lab6/lab6.ps1 -Action OpenOcd
```

应识别 RISC-V Debug Module、`XLEN=32`，并能 halt。随后从 WSL 执行准备好的 GDB 检查：

```bash
/opt/xpack-riscv-none-elf-gcc-13.2.0-2/bin/riscv-none-elf-gdb -q -nx -batch \
  -x /mnt/d/IWS/Advanced_SoC/ChipDesign/build/lab6_main_20261020/verify_cpu.gdb
```

该脚本已在H16最终配置上执行通过，包含 BootRAM 读取、SRAM 读写、寄存器设置、单步执行，
以及装载仓库现有 RV32I 零模型 TinyCNN smoke 程序、检查 `0x80001fe0 == 0xc0dec0de`。
主 SRAM 的生产 RTL 在 `SYNTHESIS` 下不预装 hex，程序必须通过调试器装载。
测试程序是 204 字节，已生成小端 binary；不能把未预装的 SRAM 内容解释为测试结果。

## 报告边界与证据

完整 DRC 保留 63 条 DSP 流水线建议和 1 条无负载网建议。
Methodology 保留 CDC/同步器属性、异步复位和 I/O delay 等普通 Warning；本次未增加 waiver 或降级告警。
时序结论对应现有约束，不是完整的板级 I/O/CDC 签核。

- 构建和资源：`build/lab6_main_20261020/summary.md`。
- 时序与完整检查：同目录 `route_timing_summary.rpt`、`route_drc.rpt`、`route_methodology.rpt`。
- 烧写：`program_board.log`、`programmed_board_properties.txt`。
- JTAG：`openocd.log`、`tck_activity.log`。
- 下载器回环：`ft232h_loopback.log`。
- 源码追溯：`source_provenance.json`。
- 仿真：`build/structure_regression_20261020_104317`。

### 更换 TCK 线后的复测

恢复了 WSL 重启后失效的 USB 转发。更换线后仍扫描全0。
已通过板载链读出 FPGA IDCODE `0362d093`，使用当前 routed DCP 导出的812位 BSDL 做只读 SAMPLE。
静态电平测量：下载器 TCK 高时 GPIO回读为 `0xf9`，FPGA E15仍为0；TDI高时E16为1，TMS低时C15为0，复位和锁定均为1。
证据为 `boundary_levels_tck_replaced.tsv`、`sample_pins_tck_replaced.log`。
尚不能区分适配器外部TCK输出、连接器接触或EGo1第17脚通路，准备交换J5的17/18两个输入做进一步定位。

### 17/18交叉测试

仅交换J5端两根输入线后，下载器TCK高电平能正确到达E16（第18脚），但下载器TDI高电平不能到达E15（第17脚）。异常跟随E15/第17脚通路，不能据此直接认定损坏；下载器TCK外部输出已得到端到端验证。
证据：`boundary_levels_swapped.tsv`、`sample_pins_swapped.log`。
准备使用空闲J5第21脚/H16替代TCK，等待用户接线确认后测量并更新约束。
