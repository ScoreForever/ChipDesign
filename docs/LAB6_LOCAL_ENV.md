# Lab 6 本地环境

配置日期：2026-10-20。依据[课程 Lab 6](https://full-stack-ai-chip.tianyuj.com/lab-6/)配置。当前分支已按用户要求切换到 `main`（CV32E40P SoC）。

板上验证已完成：本机将 **TCK 改接 J5 第21脚/H16**，第17脚留空；TDI18、TDO19、TMS20不变。
最终比特流与CPU/GDB实测证据见 [板上验证报告](LAB6_MAIN_BOARD_VALIDATION.md)。
继续使用 `build/lab6_main_h16_20261020/fpga_top.bit`；早期E15比特流不适用于当前接线。

## 本机配置

| 工具 | 位置 / 设置 | 验证 |
|---|---|---|
| Vivado 2025.2 | `D:\AMDDesignTools\2025.2\Vivado` | 能启动；识别 `xc7a35tcsg324-1` |
| Clocking Wizard 6.0 | 100 MHz 输入、50 MHz 输出、locked 开、reset 关 | 实际创建 IP 通过 |
| ModelSim SE-64 2019.2 | `C:\modeltech64_2019.2\win64` | 实际编译、仿真通过，Errors=0 |
| WSL 3.0.3 / kernel 6.18.54.1 | 发行版 `Ubuntu`，Ubuntu 24.04.3，WSL2，用户 `enovo` | 可运行 Linux 工具 |
| usbipd-win 5.3.0 | `C:\Program Files\usbipd-win\usbipd.exe` | 服务和设备查询正常 |
| RISC-V OpenOCD 0.12.0+dev-04406 | `/usr/local/bin/openocd`，启用 FTDI | 驱动与 RISC-V 目标注册检查通过 |
| xPack RISC-V GCC 13.2.0-2 / GDB 13.2 | `/opt/xpack-riscv-none-elf-gcc-13.2.0-2` | GDB 可启动，支持 RV32 |

Vivado 与 ModelSim 已在 PATH 中。启动脚本会自动寻找 Vivado，并为当前进程设置 `XILINX_VIVADO`。
usbipd 安装后的旧终端不一定能立刻刷新 PATH，脚本同时支持从默认安装目录寻找它。
WSL 的 `RISCV` 和 PATH 在 `/etc/profile.d/lab6-riscv.sh` 中配置；Bash、Zsh 启动文件追加了加载语句，原文件有 `.lab6-backup-*` 备份。
仅为 `0403:6014` 增加 udev 规则，允许现有 `plugdev` 组成员访问下载器。该 Ubuntu 使用 WSL init；已启动 udev，`Attach` 入口也会检查并启动它。

## 使用

在本仓库根目录的 PowerShell 中执行：

```powershell
# 检查版本、FTDI 配置、RISC-V 目标注册、GDB RV32 支持及 WSL USB 列表
./scripts/lab6/lab6.ps1 -Action Check

# 查看 Windows USB 下载器
./scripts/lab6/lab6.ps1 -Action UsbList

# 找到 0403:6014 对应的实际 BUSID 后执行；首次 bind 需管理员 PowerShell
./scripts/lab6/lab6.ps1 -Action Attach -BusId <实际BUSID>
```

`Attach` 会先核对 BUSID 对应 FT232H，避免误转发其他设备。插拔下载器或重启后需重新 attach。
没有连接下载器时，软件检查仍可通过；USB 转发和 JTAG 连通性不能验证。

当前 `main` 使用 CV32E40P + Debug Module；烧写对应比特流后执行：

```powershell
./scripts/lab6/lab6.ps1 -Action OpenOcd
# 另一个终端
./scripts/lab6/lab6.ps1 -Action Gdb
```

`exp/no_cpu` 分支上的专用 JTAG 控制器使用另一种协议；只有该分支及其比特流才使用：

```powershell
./scripts/lab6/lab6.ps1 -Action OpenOcd -Target nocpu
# 另一个终端
python software/host/nocpu_jtag.py status
```

两种配置不能互换。`-Target` 只选择主机连接方式，不会切换分支或 RTL。
CPU 模式使用 3333 端口；无 CPU 模式使用 OpenOCD Tcl RPC 的 6666 端口。
外部 FT232H 采用课程接线和初始 10 kHz 速率；FPGA 板载烧写 USB 保持由 Vivado 使用。

当前分支的综合、实现与比特流入口：

```powershell
./scripts/lab6/lab6.ps1 -Action Build
```

生成文件位于新的 `build/fpga_ego1_*` 目录；该命令不烧写开发板。
板上测试仍须检查对应版本的时序、DRC 和模型装载结果。
本仓库使用自己的 TinyCNN 测试固件和内存布局，运行结果应对照当前 testbench/固件，不能直接假定课程 test1 的 PASS magic 地址适用。

## 检查证据与边界

- Vivado：`build/lab6_env_check/vivado.log`，包含 `LAB6_VIVADO_CHECK=PASS`。
- ModelSim：`build/lab6_env_check/modelsim/modelsim.log`，包含 `LAB6_MODELSIM_CHECK=PASS`。
- 工具总检查：`build/lab6_env_check/tools_check.log`。
- 本次仅配置并检查开发工具；没有重新综合当前 RTL、烧写开发板或验证真实 JTAG 链。
- ModelSim 的 Vivado 全套仿真库未编译。现有 SoC 行为仿真及此次工具检查无需该库；后续仿真 Clocking Wizard 等 Xilinx IP 时再编译 Artix-7 库。

工具链下载自[官方 xPack v13.2.0-2](https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack/releases/tag/v13.2.0-2)。Linux x64 压缩包经过 SHA-256 验证：

```text
52545afb900200fbf65fe05f7ce7090b8a42c64091f4f5d43cae6bb68ea2434a
```

OpenOCD 复用已有[课程指定源码仓库](https://github.com/riscv-collab/riscv-openocd)，提交 `10b7a647da03cb64fd25db76261712c388ad6468`。
该仓库已经归档，RISC-V 支持现已并入上游；本次使用现有源码以保持课程配置一致。
原 checkout 是 CRLF，已通过 `normalize_openocd.py` 转回原始 LF 并确认无源码 diff。
编译日志在 WSL `/tmp/lab6-openocd-{bootstrap,configure,build,install}.log`。

安装依赖包括 `build-essential libftdi1-dev libusb-1.0-0-dev autoconf automake libtool texinfo pkg-config libjim-dev libhidapi-dev libcapstone-dev usbutils`。
构建使用 `./bootstrap`、`./configure --enable-ftdi --prefix=/usr/local`、`make -j8`；安装和终端环境配置记录于 `scripts/lab6/configure_wsl.sh`。

## 接入硬件后的检查（2026-10-20）

已识别外部 FT232H：`0403:6014`，本次 BUSID 为 `1-3`，序列号 `210512180081`。
已完成管理员 bind 和 WSL attach，Windows 状态为 Attached；Ubuntu 中识别为 FT232H，USB 节点权限为 root:plugdev 0660，普通用户可访问。
首次 attach 后内核枚举存在短暂延迟，启动脚本已加入等待。
10 kHz 扫描日志：`build/lab6_env_check/jtag_scan.log`，当前返回 all ones。
用户确认 EGo1 已上电、尚未烧写，因此暂不具备 FPGA fabric 内的 SoC TAP；USB 转发正常不等于 CPU JTAG 已连通。
下一步应生成、审查并烧写当前 main 的比特流，再检查 TAP、CPU XLEN 和 GDB。
Vivado 板载下载链检查通过：识别到 `xc7a35t_0`；明确报告 `is not programmed (DONE status = 0)`。日志：`build/lab6_env_check/board_probe.log`，设备属性：`build/lab6_env_check/board_properties.txt`。本次未烧写或修改 FPGA。
