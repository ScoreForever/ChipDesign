# Findings

- `main` 当前 HEAD 为 `9c0096a`，`origin/exp/no_cpu` 是其祖先。
- 当前生产文件表 `hardware/soc/sim/filelists/chipdesign_nocpu.f` 仅包含 NPU、control 和 `my_soc_top.sv`，不包含 CPU、AXI、DMA、Debug。
- 当前 `hardware/soc/rtl/my_soc_top.sv` 仅实例化 JTAG transport 和 NPU control subsystem。
- CPU/AXI/Debug/DMA 文件来自早期提交：CPU/Debug/SoC `ea29e6f`，DMA `9ca44a0`；no_cpu 改造提交 `48958c0` 保留了旧系统作为参考。
- `hardware/soc/sim/filelists/chipdesign_soc.f` 已在 `c24b342` 删除；当前仓库仍有少数旧回归脚本引用它，需单独处理，不能把它误认为 no_cpu 生产入口。

- 已归档 206 个 Git 跟踪文件，全部为 R100（内容不变）迁移。
- 当前 `hardware/soc/rtl` 仅保留 `control/` 与 `my_soc_top.sv`；当前 SoC 测试台仅保留 `tb_npu_program.sv`。
- 旧脚本内部原布局引用按历史快照保留，归档 README 明确禁止将其视为当前运行入口；完整历史工作区基线为 `6957d2ce28798fb8697d3b64691bbc6cde8061bb`。
- 当前 DC/Genus 搜索路径移除了已归档的旧 include；旧 CDC 单元验证的显式源路径已更新。
