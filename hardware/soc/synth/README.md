# SoC 综合入口

本分支 DC/Genus 入口已选择 `chipdesign_nocpu.f` 和无 CPU `my_soc_top`。
2026-10-20 默认权重方案为一颗 `RA1SHD_2048x32M8`，具体配置及物理交接见
[面积优化实验](../../../docs/NOCPU_AREA_OPTIMIZATION_20261020.md)。
FPGA 默认使用 `hardware/fpga/scripts/build_nocpu.py`，详见
[CPU_LESS_CONTROL.md](../../../docs/CPU_LESS_CONTROL.md)。

从仓库根目录运行。服务器先 `source /opt/eda/env.sh`。

```sh
export EDA_RESULT_DIR=/path/to/new-results
mkdir -p "$EDA_RESULT_DIR"
genus -no_gui -batch -files hardware/soc/synth/scripts/synth_genus.tcl > "$EDA_RESULT_DIR/console.log" 2>&1
```

未设置 `EDA_TARGET_LIBS` 时，Genus 仅做 `read_hdl`，成功标记为
`EDA_RTL_READ_COMPLETE`；Genus 的 elaboration 本身要求技术库，不能把读入检查
当作完整综合。Genus 可能在脚本失败后仍以 0 退出，必须同时检查成功标记和 Error 日志。

映射综合前，明确设置以下变量：

- `EDA_TARGET_LIBS`：Tcl 列表；Genus 使用 Liberty 文件，DC 使用 `.db`。
- `EDA_CLOCK_NS`：功能时钟周期。
- `EDA_IO_DELAY_NS`：系统级输入/输出延迟预算。
- `EDA_CLOCK_UNCERTAINTY_NS`：时钟不确定度。

有技术库时 Genus 执行 elaborate、syn_generic、syn_map、syn_opt，产出网表、SDC、
面积和时序报告；成功标记为 `EDA_MAPPED_SYNTHESIS_COMPLETE`。DC 备用入口为
`dc_shell-xg-t -f hardware/soc/synth/scripts/synth_dc.tcl`；无库时仅 generic elaboration。
本轮更新后的 Genus/DC 入口未实际执行，只通过模拟工具命令检查了 Tcl 配置/失败分支。
默认 Genus 映射自动加入课程 SRAM TT Liberty 和 `NPU_USE_RA1SHD_WEIGHT`；
可用 `EDA_WEIGHT_SRAM_LIB` 覆盖路径。DC 则必须另提供 `EDA_WEIGHT_SRAM_DB`。
缺失宏库会明确失败，不自动退回行为 RAM。`EDA_NPU_WEIGHT_SRAM=0` 可显式选择 FF 对照，
`EDA_NPU_TILE=16/8/4`、`EDA_NPU_ARRAY_ACC=18/32`、`EDA_NPU_PSUM=23/32` 可选择实验配置。

当前约束模板包含功能时钟、异步 JTAG 时钟和双域 I/O 预算；JTAG 周期默认
1000 ns，可用 `EDA_JTAG_CLOCK_NS` 修改。最终约束仍需板级复位、驱动/负载、
bundled-data CDC 物理延迟预算等签核。无 CPU 系统不再使用共享主 SRAM；
NPU 权重已经接入宏 wrapper，激活与小缓冲仍是 RTL 存储；PnR 必须加入匹配物理视图，
并适配新宏路径 `i_control/i_npu/g_weight_sram/storage/i_sram`。
这些脚本不替代 FPGA Vivado 流程，也不是时序签核结果。
