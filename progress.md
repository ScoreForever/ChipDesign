# Progress

- 2026-10-28: 完成分支、filelist、顶层和历史提交盘点；尚未移动文件。
- 2026-10-28: 将 `hardware/cpu` 与 `hardware/soc/rtl/{axi,clk_rst,common_cells,debug,dma,include,mem}` 移到 `hardware/legacy/cpu_soc/`，新增历史目录说明。
- 2026-10-28: 更新 CDC 验证脚本和进展报告中的 DMA 路径。

- 2026-10-28: 归档旧 SoC 仿真脚本和 8 个旧测试台；补充主 README、SoC README 与仿真 README 的历史目录导航及复现边界。
- 验证：206 个 R100 迁移、生产文件表字节完全一致、所有生产源文件均存在、`git diff --check HEAD` 通过。
- 验证：`python3 hardware/npu/scripts/run_program_regression.py --quick --output build/legacy_archive_regression_20261028` 通过，33 项场景；还包括 20000 个量化向量及双 SRAM、trace、背压单元验证。
- 验证：Icarus 运行归档后的独立 DMA 测试，通过 LEN=1、延迟尾响应、重启与 clear_done。
- 未运行 ASIC/FPGA 综合或历史整机回归；当前生产 RTL 与文件表未改。
