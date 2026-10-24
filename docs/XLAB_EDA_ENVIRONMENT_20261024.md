# xlab 远端 EDA 环境检查（2026-10-24）

当前主力已切换为 [No-CPU 可编程 NPU 综合](NOCPU_EDA_SYNTHESIS_20261024.md)。
本文以下为先前主线环境验证记录；带 CPU 的综合和自动后续验证已经停止，日志保留。

正确节点为 `tc-01a0e839fa89`，连接方式：

```bash
ssh -p 10416 s2400012892@10.129.70.18
```

本机 `C:/Users/Lenovo/.ssh/config` 中的 `xlab` 已改为上述入口；原配置备份为
`config.backup-eda-20261024-141534`。可以继续直接执行 `ssh xlab`。
沿用已存在且在两个节点间共享的工作目录：

```text
/home/s2400012892/chip-fullstack-2026/ChipDesign_main_eda_20261024_5204d3f
```

该目录使用主线 `5204d3ffda8e255ce12c3f91f2b9911df60f14cd` 的 CV32E40P SoC，
与当前本地 `exp/no_cpu` 分支不同；本轮没有更换远端源快照或修改 RTL。

## 已验证

| 检查 | 结果 |
| --- | --- |
| Genus 19.17-s144_1 启动/许可证 | PASS |
| Innovus 21.16-s078_1 启动/许可证 | PASS |
| Xcelium 21.09-s013 编译及运行 smoke | PASS |
| 主线 Genus RTL 读入 | 先前节点 PASS；正确节点已完成带库 elaborate，无 unresolved references |
| 主线 Xcelium ASIC 配置结构回归 | 7/7 PASS |
| 综合、PNR、后仿真输入预检 | 18/18 输入可读，PASS |
| 主线映射综合 | 已启动，结果待确认 |
| Innovus 实际物理库导入 smoke | PASS，0 errors；未执行布局布线 |

7 项回归为重量化、非对称容量、4096 容量、整 SoC、DMA、SRAM 三拍读、SRAM 一拍读。
整 SoC 程序测试通过于 32,226 cycles，DMA 测试通过于 725 cycles。
7 项回归已在正确节点重新执行。它们是 RTL 仿真，不代表 PNR 或 SDF 仿真通过。

## PDK 与 SRAM

正确节点的 `/lec/pdk/tsmc180` 和 `/lec/lab4` 均存在，`/lec` 挂载课程
`01a0e716-6be4-7a0f-b663-b0e46e3d1242` 的 content 目录。
标准单元、IO/PAD 的 Liberty/LEF/GDS/Verilog、RC captable 和 GDS layer map 均可读。

此前 `xlab` 指向 `10.129.70.17:11152` 的 `tc-01a0c36a652b`，
其 `/lec` 挂载的是另一课程 `01a0c01e-8c95-7aeb-9dbf-780b7e8ab4af`，没有 PDK。
路径差异由节点/课程挂载不同造成。

逐文件核对私有 SRAM 的 TT Liberty、vclef、antenna LEF、Verilog、CDL 与课程目录，
五项均完全一致。已从 `/lec/lab4/soc_cv32e40p_template_pnr-stu/src/macro/RA1SHD_2048x32M8/`
补齐 GDS，SHA256 为 `2b3cb5a4c098951fced63907cf38ed6a33ac10d2b0cc856e707dc2168a02c521`。
Innovus 已实际加载标准单元、IO/PAD、SRAM 的 LEF/Liberty，并导入包含 buffer 和 SRAM
的最小网表，`EDA_PDK_LIBRARY_IMPORT_PASS`，共 1217 warnings、0 errors。
警告包含重复 MACRO、LEF/Liberty 中部分物理引脚的定义差异，以及 METAL7/METAL8
在六层技术 LEF 中不存在（IMPLF-63）。原始库未作删层修改；导入成功不代表这些警告已关闭。
GDS 结构与 layer/datatype 清单保存于 `logs/sram_gds_layers.json`，尚未做 DRC/LVS。

## 本轮远端入口修正

- `run_stage.sh preflight [synthesis|pnr|postlayout|all]` 支持按阶段检查。
- 预检 JSON 增加节点、时间、PDK 路径；SRAM GDS 必须可读且非空。
- `configure.py` 同时生成后仿真脚本，使用 `CHIPDESIGN_PDK_ROOT`，并在重载 EDA 环境时保留课程许可证覆盖。
- 生成配置将原模板的 `pnr_cpus=16` 调整为当前 Innovus 许可证允许的 8。
- RTL、综合、PNR、后仿真入口增加超时和完成标记检查；综合交接与后仿真运行前检查所需产物。

远端原始入口备份于 `backups/env-update-20261024-wpl8qc/`。
修改的 Python/Bash 语法检查、更新后 RTL 入口、缺库停止分支均通过验证。
PNR/后仿真成功分支尚未在该节点验证。
`bootstrap_xlab.py` 是原始建站脚本，未同步本轮入口修正；重新运行可能覆盖这些修正。

## 继续运行

在服务器上执行：

```bash
source ~/chip-fullstack-2026/ChipDesign_main_eda_20261024_5204d3f/env.sh
run_stage.sh rtl
run_stage.sh preflight synthesis
```

默认 PDK 已可用，运行顺序为：

```bash
run_stage.sh preflight synthesis
run_stage.sh synth
run_stage.sh pnr-import
run_stage.sh preflight pnr
run_stage.sh pnr
run_stage.sh postlayout
```

每步成功后再进行下一步。其他 PDK 位置可用 `CHIPDESIGN_PDK_ROOT` 覆盖。
当前整 SoC 综合仍在运行；一次性后续作业 `bin/finish_validation.sh` 会在本轮综合进程退出后，
核对成功标记和交接网表，再执行 `pnr-import`，保存最终状态。它不会启动完整 PNR 或 SDF 仿真。
可用以下命令查看状态：

```bash
tail -n 30 "$CHIPDESIGN_EDA_ROOT/logs/synthesis.log"
tail -n 30 "$CHIPDESIGN_EDA_ROOT/logs/finish_validation.log"
cat "$CHIPDESIGN_EDA_ROOT/logs/environment_status_20261024.json"
```

`logs/final_validation_exit_code.txt` 在后续作业退出后生成；0 表示通过。
运行中的 JSON 中 `mapped_synthesis_passed=false` 表示尚未通过，不应直接解释为已失败。
当前场景为 20 ns 核心时钟、2 ns IO 延迟和 0.2 ns uncertainty；主线约束在综合场景中
固定 JTAG 不活动，需要另行确认实际运行模式。Genus 还报告当前 OS 不在其支持清单中；
本轮启动和 RTL 验证通过不等于正式流程兼容性验证完成。

## 证据

远端 `logs/environment_status_20261024.json` 保存源版本、检查状态、缺失项和日志/入口 SHA256。
工具日志位于 `logs/`，RTL 日志为 `logs/rtl_read/run.log`。
正确节点 7 项回归位于 `source/build/eda_environment_tc-01a0e839fa89_20261024_061814/`。
此前节点的证据归档保留为 `logs/environment_evidence_20261024.tar.gz`。
本地副本位于 `work/xlab-eda-20261024/`（被 Git 忽略）；未复制工艺库到仓库。
