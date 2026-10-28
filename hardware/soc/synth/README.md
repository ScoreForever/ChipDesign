# 无 CPU 双 SRAM 综合入口

当前 DC/Genus 入口读取 `chipdesign_nocpu.f`，顶层为 `my_soc_top`。
默认模型区和激活区各8 KiB；映射综合同时实例化两颗 `RA1SHD_2048x32M8`，
分别位于 `i_control/i_model_store/i_sram` 与 `i_control/i_activation/i_sram`。
同一宏类型的Liberty/DB只加载一次，不为第二颗实例重复列出库。
激活端保留64 B读缓存、写合并/转发与小队列，完整张量不再由标准单元寄存器阵列保存。

从仓库根目录运行。课程服务器先加载已配置的EDA环境，例如 `source /opt/eda/env.sh`：

```sh
export EDA_RESULT_DIR=/path/to/new-results
mkdir -p "$EDA_RESULT_DIR"
genus -no_gui -batch -files hardware/soc/synth/scripts/synth_genus.tcl > "$EDA_RESULT_DIR/console.log" 2>&1
```

未设置 `EDA_TARGET_LIBS` 时，Genus仅做 `read_hdl`，成功标记为 `EDA_RTL_READ_COMPLETE`；
elaboration要求技术库，不能把读入检查当作完整综合。Genus可能在脚本失败后仍以0退出，
应同时核对成功标记和Error日志。

映射综合所需变量：

- `EDA_TARGET_LIBS`：Tcl列表，Genus使用Liberty，DC使用DB。
- `EDA_CLOCK_NS`、`EDA_IO_DELAY_NS`、`EDA_CLOCK_UNCERTAINTY_NS`：功能时钟、I/O延迟和不确定度预算。
- `EDA_NPU_MODEL_SRAM=1`：默认设置，同时定义 `NPU_USE_RA1SHD_MODEL` 和 `NPU_USE_RA1SHD_ACTIVATION`。
- `EDA_MODEL_SRAM_LIB`：Genus宏Liberty覆盖路径；默认采用课程TT库。
- `EDA_MODEL_SRAM_DB`：DC必须提供对应宏DB。
- `EDA_NPU_COLS=4|8`：选择共享阵列列数。
- `EDA_NPU_WAVE_TRACE=0|1`：可选覆盖波形RAM构建开关；默认不设置，ASIC综合自动关闭波形RAM，
  保留性能计数器和逐算子摘要。显式开启会带回16 KiB波形存储，其ASIC宏化和面积需另行评估。
- `EDA_NPU_OP_TRACE=0|1`：覆盖逐算子摘要开关；默认不设置，ASIC综合自动关闭（约0.2 mm²），
  全局性能计数器保留，`0x1a8`报告0。

现有环境变量名中的MODEL保留构建入口名称，该宏库实际供模型和激活两颗实例共用。
`EDA_NPU_MODEL_SRAM=0` 显式选择行为存储对照；ASIC工具仍可能把它映射成大量标准单元，
该对照不代表双硬宏方案的面积。缺失宏库时明确失败，不会自动退回行为存储。

有技术库时Genus执行elaborate、syn_generic、syn_map、syn_opt，输出网表、SDC、面积和时序；
成功标记为 `EDA_MAPPED_SYNTHESIS_COMPLETE`。DC备用入口为
`dc_shell-xg-t -f hardware/soc/synth/scripts/synth_dc.tcl`。

约束包含功能时钟、异步JTAG时钟和双域I/O预算；JTAG默认1000 ns，可用
`EDA_JTAG_CLOCK_NS` 修改。宏Liberty/DB需与物理LEF/GDS及角落匹配。
当前checked-in课程PnR模板仍含旧 `i_mainmem_i_mainmem` 单宏位置和电源连接，
不能直接当作双宏版的物理交接：需针对上述两个新实例重新规划位置、halo、block ring及供电。
本次用户目标为2×2 mm²，仅用于面积估算和设计选择，不以EDA面积严格达标作为验收条件。

FPGA入口为 `hardware/fpga/scripts/build_nocpu.py`，未定义ASIC宏时由同步RAM推断映射；
仿真、综合、PnR/STA及板级结果应分别记录。当前实施记录见
[双SRAM实施与验证](../../../docs/NPU_DUAL_SRAM_IMPLEMENTATION_20261026.md)。
