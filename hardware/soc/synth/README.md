# SoC 综合入口

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
本次 soc 的 DC 报 DCSH-1，尚未实测该流程；Genus 映射分支也尚未实测。

约束模板仅描述 JTAG 不活动的功能场景。最终约束还需具体复位策略、JTAG 场景、
时钟域关系、驱动/负载以及系统 I/O 预算。现有 CPU clock gate 是 FPGA 直通实现，
主 SRAM 是可推断存储；ASIC 交付还需技术相关 clock gate 和 SRAM macro 适配。
这些脚本不替代 FPGA Vivado 流程，也不是时序签核结果。
