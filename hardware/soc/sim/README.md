# SoC 与仿真

当前 SoC 是无 CPU、ABI 5.0 的描述符 NPU，源文件表为
`hardware/soc/sim/filelists/chipdesign_nocpu.f`。

```powershell
python hardware/npu/scripts/run_program_regression.py --output build/program_regression
```

测试台 `tb_npu_program.sv` 用同一主机命令轨迹验证原生握手和专用 JTAG 引脚。
数值、随机延迟、量化和存储单元验证由同一回归入口驱动。

旧 CPU/MMIO 集成和回归可从 Git 历史获取。

详见 [架构](../../../docs/ARCHITECTURE.md) 与 [控制协议](../../../docs/CPU_LESS_CONTROL.md)。
