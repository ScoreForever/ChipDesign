# SoC 仿真

生产SoC仿真入口为 `filelists/chipdesign_soc.f` 和
`scripts/run_soc.ps1`。它编译CPU、AXI、SRAM、DMA、当前GAP路径TinyCNN-8 NPU及
`chipdesign_soc_tb`。

从仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1
```

测试程序 `sw/chipdesign_npu_test.hex` 由以下命令生成：

```powershell
D:\conda\envs\ml\python.exe hardware/soc/sim/sw/gen_hex.py `
  --output hardware/soc/sim/sw/chipdesign_npu_test.hex
```

CPU通过正式
MMIO窗口装入全零输入和模型，启动一次四分类完整推理，检查完成状态、NPU中断
和四个INT32零logit，最后在SRAM魔数区域写入PASS或FAIL。

当前MMIO仍拒绝GAP层2参数提交，GAP参数数组不复位初始化。上述全零smoke
不能证明非零模型可经MMIO部署；核直接端口的GAP测试见下方结构回归入口。

脚本不仅检查 ModelSim 进程返回值，还要求日志出现测试台的明确 PASS 标记，并把
FAIL、TIMEOUT 或非零 Errors 视为失败，防止 `$finish` 导致假阳性。

完整系统需要支持SystemVerilog interface的ModelSim/Questa/VCS。独立NPU和
MMIO wrapper测试使用Icarus，可运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_all_tests.ps1
```

旧的Lab3测试文件仍保留作课程参考，但不是当前TinyCNN-8生产回归入口。

结构优化的 ModelSim 回归（重定量、激活存储容量、SoC、DMA、AXI/SRAM 流水线）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_structure_regression.ps1
```

该入口创建独立仿真库，并关闭 VCD 记录。资源对比和测试边界见
[`FPGA_STRUCTURE_OPTIMIZATION.md`](../../../docs/FPGA_STRUCTURE_OPTIMIZATION.md)。
后续 DRC 修正和读延迟代价见
[`FPGA_DRC_CLOSURE.md`](../../../docs/FPGA_DRC_CLOSURE.md)。
