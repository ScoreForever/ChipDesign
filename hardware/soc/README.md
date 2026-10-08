# ChipDesign SoC 集成

本目录包含 CV32E40P CPU、AXI 互连、BootRAM、SRAM、JTAG Debug、DMA 和
完整 KWS-TinyCNN-8 NPU 的片上系统。

模型架构、量化规则、模块分工、MMIO寄存器和软硬件职责边界以
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) 为准。

## 正式NPU数据通路

```text
CPU / DMA
   |
   v
tinycnn8_npu_mmio_wrapper
   |
   v
tinycnn8_npu_top
   |
   +-- Conv1 -> Pool1 -> Conv2 -> Pool2 -> NHWC Flatten -> FC(160->4)
```

算子级的旧 `npu_mmio_wrapper.sv` 已删除：它从未进入正式 SoC filelist，正式 SoC
只包含一套计算单元。需要算子级调试请使用 `hardware/npu/` 下的 Icarus 单元回归。

## 目录

```text
hardware/soc/
├── rtl/
│   ├── axi/、common_cells/、clk_rst/、debug/、mem/
│   ├── dma/npu_dma.sv
│   ├── npu/tinycnn8_npu_mmio_wrapper.sv
│   ├── my_npu_subsystem.sv
│   └── my_soc_top.sv
└── sim/
    ├── filelists/chipdesign_soc.f
    ├── scripts/run_soc.ps1
    ├── sw/gen_hex.py
    ├── sw/chipdesign_npu_test.hex
    └── tb/chipdesign_soc_tb.sv
```

## 地址空间

| 区域 | 基址 | 说明 |
| --- | --- | --- |
| Debug | `0x0000_0000` | RISC-V Debug Module |
| BootRAM | `0x0001_0000` | 启动代码 |
| NPU | `0x7000_0000` | 16 KiB完整NPU MMIO窗口 |
| SRAM | `0x8000_0000` | 当前8 KiB主存 |

## 验证

完整NPU和MMIO wrapper可用Icarus回归：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_all_tests.ps1
```

集成SoC使用ModelSim：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/soc/sim/scripts/run_soc.ps1
```

CPU测试程序通过正式MMIO窗口装入全零输入和模型，启动一次四分类完整推理，
检查NPU完成中断与四个零logit，并清除done。测试镜像由
`hardware/soc/sim/sw/gen_hex.py`生成。

当前MMIO ABI版本为`0x0002_0001`（Flatten ABI + 性能计数器）。参数层2是已移除GAP留下的保留槽，提交
会返回错误码6；正式软件只提交Conv1（层0）、Conv2（层1）和FC（层3）。

SoC使用SystemVerilog interface和断言，不能用Icarus编译整个SoC；Icarus仍用于
独立NPU和MMIO wrapper回归。
