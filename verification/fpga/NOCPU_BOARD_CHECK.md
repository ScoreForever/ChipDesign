# No-CPU FPGA 人工上板与证据复核

本流程针对 ABI 3.0 INT4 和当前无 CPU NPU。`nocpu_board_check.py` 实现的是 **JTAG 能力读取与重复推理 smoke test**，不是完整的板级签核套件，也不自动烧录、改变复位状态或核实 FPGA 当前配置帧。

## 1. 构建并复核

核对源代码变更及 EGo1 板卡、FT232H 接线、供电和共地；TCK 等管脚必须与本次 XDC 匹配。先运行 RTL 回归，再使用 Vivado 生成新的 50 MHz bitstream：

```powershell
python hardware/npu/scripts/run_program_regression.py --output build/nocpu_rtl_manual
python hardware/fpga/scripts/build_nocpu.py --vivado-root D:/Project/Vivado/2025.2/Vivado --output build/nocpu_manual_build --jobs 4 --mhz 50 --implement
```

输出目录必须是新目录；按机器调整 Vivado 路径。查看仿真结果及同次构建的 `summary.md`、布线/时序、CDC、外部 I/O、未约束路径和 DRC 报告，记录未关闭问题。不要把生成 bitstream 等同于时序或实板通过。

## 2. 下载和连接

用 Vivado Hardware Manager 将审查过的 bitstream 下载到 FPGA，保存编程日志并人工记录目标器件、时间、配置状态、bitstream 路径和 SHA-256。FPGA 配置线与 FT232H NPU JTAG 是不同连接。确保记录的 bitstream 来自上述同一次构建；脚本不能自动证明板上正在运行哪个镜像。

项目既有 EGo1 接线记录为 J5: TCK 21/H16、TDI 18、TDO 19、TMS 20、GND 33/34、VREF 35/36。此信息仅供核对；最终以实际板卡和本次 XDC 为准。

启动 OpenOCD 并保留控制台日志（根据安装环境调整命令）：

```bash
openocd -f software/host/lab6_ft232h.cfg -f software/host/nocpu_openocd.cfg
```

`nocpu_openocd.cfg` 会将 TCK 设为 1000 kHz。确定接线和时序适用于这个设置；如需降低频率，请使用自己的配置副本并记录实际设置。配置下载线的 FPGA IDCODE 与 NPU fabric TAP 的 IDCODE 是不同对象。

## 3. 准备模型和参考输出

```powershell
python software/host/make_demo.py --output build/nocpu_manual_demo
```

默认示例按 4×8 编译；若硬件为 4×4，保留同一个示例输入及 expected.bin，重新打包模型：

```powershell
python software/host/npu_program.py build/nocpu_manual_demo/model.json --cols 4 --output build/nocpu_manual_demo/package4
```

参考输出来自项目的软件参考模型，适合 smoke test；若要提升验证独立性，应另用独立 golden 或已知答案交叉核对。模型、输入和参考输出必须相互对应。

## 4. 实板运行

在项目根目录、另一个终端运行。下例路径必须替换为**本次构建实际生成的 bitstream**，并使用该文件 SHA-256：

```powershell
$bit = 'build/nocpu_manual_build/<实际文件名>.bit'
$hash = (Get-FileHash -Algorithm SHA256 $bit).Hash
python verification/fpga/nocpu_board_check.py `
  --bitstream $bit --bitstream-sha256 $hash `
  --provenance build/nocpu_manual_build/provenance.json `
  --package build/nocpu_manual_demo/package `
  --input build/nocpu_manual_demo/input.bin `
  --expected build/nocpu_manual_demo/expected.bin `
  --output build/nocpu_board_check_01 --repeat 3
```

使用 4×4 时将 `--package` 改为 `package4`。脚本要求新证据目录，要求 NPU 初始状态空闲且未配置；必要时在人工确认后用 `python software/host/nocpu_jtag.py reset` 恢复。复位会使当前模型和输入失效。初始状态不符合要求时脚本不会自动复位。

退出码 0 表示脚本范围内全部通过，1 表示失败。`report.json` 包含能力值、输入及输出哈希、逐次比较和错误信息；证据目录还保存模型、输入、参考值、manifest 和每次实际输出。保存并关联 OpenOCD 日志、Vivado 编程日志、bitstream、构建报告与本报告。脚本校验当前源码与 provenance 中列出的源码哈希，也校验传入 bitstream 的哈希，但 **不验证该文件与 provenance 的构建关系或板上镜像身份**，这两项需人工用同次构建、下载日志和配置记录确认。

## 5. 复核并补充测试

- 审阅 `report.json` 中设备能力、每次输出、最终状态及实际/期望文件。若失败保留原目录，排查后使用新目录重跑。
- 对目标版本另行覆盖 Conv、FC、Pool、ReLU、Requant、融合/非融合、数值和容量边界，逐字节与独立参考比对。
- 单独验证非法描述符、输入/模型不完整、结果未 ACK、忙时请求、watchdog 与协议 RESET，并保留预期错误和恢复证据。
- 在安全条件下安排物理复位、断电重配、TCK 暂停、长时间连续运行及必要的电气测试；不得将协议 RESET 等同于物理按键复位。
- 汇总各阶段结论与尚未测试项目。通过本 smoke test 不代表这些补充项目、物理时序裕量或完整板级签核通过。

历史 `verification/fpga/run.ps1` 主要针对 CPU/GDB 方案，不可作为当前 No-CPU 双 SRAM 版本的上板通过证据。

## 6. 扩展实板自动回归（Windows）

保持 OpenOCD 运行，确认 NPU 空闲且无未 ACK 结果或故障；必要时人工检查后执行 reset。使用已下载的本次 bitstream 和一个尚不存在的结果目录：

```powershell
$bit = 'build/nocpu_manual_build/<实际文件名>.bit'
$hash = (Get-FileHash -Algorithm SHA256 $bit).Hash
python verification/fpga/nocpu_board_suite.py `
  --bitstream $bit --sha256 $hash `
  --provenance build/nocpu_manual_build/provenance.json `
  --output build/nocpu_board_suite_01 --repeat 2 --timeout 30
```

脚本读取 ABI、容量和列数，按实际 4×4 或 4×8 编译既有回归模型。覆盖五类算子、融合/非融合、尾通道、负数、量化边界、padding/stride、长 K、INT32、完整 8 KiB 激活区，以及同一模型重复运行（输入原序/逆序）。TinyCNN 非融合超过 8 KiB 时记录为预期主机拒绝，其他编译跳过使本轮不通过。逐组保存包、输入、参考和实际输出，并保存命令事务日志。脚本检查构建 provenance 中列出的源码哈希与当前源码是否一致。错误用例检查未装载启动、输入未完成、乱序写、模型未完成、非法描述符、结果未 ACK，并在错误恢复后再次执行合法推理。失败立即停止并写 `report.json`，不得将未运行项当作通过。

参考值来自仓库软件参考实现；TinyCNN 用例还在生成时与另一套历史整数 golden 交叉比对，其他算子不应宣称全部独立。bitstream SHA 是文件哈希，脚本无法证明当前板上配置帧与其一致；保留下载日志并人工核对版本。按测试数量与 JTAG 速度，完整回归可能耗时较长。保留 OpenOCD 输出，若网络/命令超时，先检查状态，切勿自动重发 START 或 COMMIT。

**尚需人工单独执行并存证：** busy 状态下重复 START、watchdog 超时和恢复、busy 时 RESET、确认 busy 后暂停 TCK、物理复位按钮、断电重启和电气/温压测试。脚本报告的 `manual_pending` 明确列示部分未自动执行项；自动回归 `passed=true` 仅表示自动范围通过，不能代替这些实板检查或完整签核。遇到任何功能失败保留目录、日志及设备状态，重新执行时使用新目录。

## 7. 独立复核自动结果

完成自动套件后运行：

```powershell
python verification/fpga/nocpu_evidence_check.py build/nocpu_board_suite_01
```

复核器重新读取结果文件，检查预期算子用例和负向用例清单、每次输入/参考/输出的哈希，以及参考与输出是否相等。退出码 `1` 表示证据损坏、不完整或不匹配；`2` 表示自动证据核对通过，但物理操作和板上镜像身份仍需人工复核。这个复核器不重新运行硬件、不验证参考模型的独立性，也不保证报告不可伪造。保存全部证据目录，避免只转发截图或 `passed` 标志。

实板人工项至少记录执行者、时间、bitstream SHA-256、测试前状态、操作、实际观测、预期结果和原始日志路径。协议 RESET、物理复位与断电重配必须分开记录；未执行一律标记 NOT RUN。报告中的 `manual_pending` 是测试范围提示，不能通过编辑它来声称物理测试通过。
