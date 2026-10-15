# soc 服务器 EDA 部署（2026-10-14）

独立目录：`/home/s2400012892/chip-fullstack-2026/ChipDesign_20261014_fused/`。
源基线 `7c1348c`，叠加 Linux Xcelium runner、综合脚本和顶层 INIT_FILE 参数兼容修改。
已部署优化模型包与 100 条真实语音向量；本轮整 SoC 运行的是既有程序 smoke，
并未在 CPU/DMA 通路运行这 100 条语音。

## 执行结果

Xcelium 21.09-s013，最终修改后重新运行全部 7 项：

| 测试 | 结果 |
| --- | --- |
| CPU→MMIO→融合 NPU，并检查 IRQ | PASS，40,615 cycle |
| DMA 程序 smoke | PASS，917 cycle |
| 重量化单元 | PASS |
| 非对称激活容量 | PASS |
| 4096/4096 激活容量 | PASS |
| SRAM 3 拍读流水线 | PASS |
| SRAM 1 拍读兼容模式 | PASS |

日志位于 `results/structure_final/`，摘要及源 hash 见
[机器可读结果](validation/soc_eda_20261014.json)。本地原始日志保留在
`work/soc-eda-20261014/`。

使用仓库 `hardware/soc/sim/scripts/run_structure_regression.py --output <新空目录>`，
运行前 `source /opt/eda/env.sh`。检查工具退出状态、PASS 和 Error/Fatal/FAIL/TIMEOUT。

## 综合准备与限制

Genus 19.17-s144_1 许可证可用。首次读入不支持顶层 `parameter string INIT_FILE`；
改用与其他存储模块相同的无类型文件路径参数，读入检查通过。修改后整 SoC 回归仍通过。
Genus elaboration 要求技术库，因此当前仅完成 RTL 读入，未进行映射综合。
最终日志位于 `results/genus_read_final/console.log`，含 `EDA_RTL_READ_COMPLETE`。

Genus / DC 入口与约束模板见
[综合说明](../hardware/soc/synth/README.md)。DC / dc_shell-xg-t 报
`Fatal: Design Compiler is not enabled. (DCSH-1)`；VCS 缺 `libelf.so.1`。
未修改共享工具安装与许可证。未指定目标工艺库，无面积 / STA 报告。

Xcelium 警告：`addr_decode.sv` 一项 deferred final 断言不受支持；部分端口未连接、
字符串到整数参数转换，以及默认 VCD 可见性限制。7 项测试通过不代表这些警告已关闭。
调试需要完整信号时添加 `+access+r`；尚未专项替代该断言。

本次未在 soc 运行 Vivado，未生成 FPGA bitstream，也未做真实上板。
当前状态的交流版见 [FPGA HTML 进度报告](FPGA_VALIDATION_PROGRESS.html)。
