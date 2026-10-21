# Baseline post-layout metrics — aligned to `origin/main` (128d454)

本文件补齐一处空洞：仓库根目录的 `POSTLAYOUT_METRICS_BASELINE.md` 记录的是 RTL `9d25b096` 的
结果，而 `main` 之后又前进了 2 个提交。本报告用 `main` 的最新 RTL 重新跑完
Genus → Innovus → DRC/天线 → SDF 门级后仿，给出与 `main` 对齐的同一套指标。

## 0. 结论摘要

- `main` 的 RTL 相对 `9d25b09` 只改了 **3 个文件、净 +10 行**；新增的 TCK 域复位同步器
  实测只占 **226 µm²（0.006%）**，`ASYNC_REG` 是 Vivado 属性、Genus 忽略。
- 用 `main` 的 RTL 完整重跑后：**Innovus DRC 0、process antenna 0、setup WNS +0.886 ns、
  hold WNS +0.020 ns、SDF 后仿 PASS（32,226 周期）**。
- 后仿收尾周期数与旧基线 **完全一致（32,226）**，说明这 14 行 RTL 改动不影响功能。
- 绝对面积/时序与旧基线有差异，来源是**综合映射配置漂移**，不是 RTL：
  post-route `report_area` 3.647 vs 3.669 mm²（−0.6%），但综合单元数 104,941 vs 94,601（+10.9%）、
  setup 余量 +0.886 vs +3.625 ns。旧基线的 Genus 配置未随流程保留，无法逐位复现。

## 1. 运行标识

| 项目 | 值 |
|---|---|
| RTL 分支 / 提交 | `origin/main` @ `128d454` |
| 对比基线 | `9d25b09`（`POSTLAYOUT_METRICS_BASELINE.md`） |
| 顶层 | `my_soc_top`（CV32E40P + AXI + DMA + 8 KiB 主存 + 融合 NPU，`FUSED_CORE=1`） |
| 流程目录 | `/home/s2400012724/chip-fullstack-2026/ChipDesign_postsim_exps/pnr_main_align_std` |
| 综合 | Genus 19.17-s144_1，`syn_opt_level standard`，16 CPU |
| 布局布线 | Innovus 21.16-s078_1，`pnr_opt_level standard`，16 CPU |
| 后仿 | Xcelium 21.09-s013，`-access +rwc`，MAXIMUM SDF |
| 工艺 | TSMC 180 nm，`tcb018gbwp7t`，`RA1SHD_2048x32M8`（8 KiB） |
| 视图 | 综合 `func_tt_1p80v_25c`；IO 集成后 `func_tt_io` |
| 时钟 | `core_clk` 20 ns（50 MHz）、`jtag_clk` 100 ns、`virtual_jtag_tck` 1000 ns |

## 2. RTL 差异（9d25b09 → 128d454）

| 文件 | 改动 | 实测影响 |
|---|---|---|
| `hardware/soc/rtl/my_soc_top.sv` | +8 行：新增 `rstgen i_rstgen_tck`（TCK 域），调试传输复位改用 `rst_tck_n` | 新增实例 `i_rstgen_tck`：**8 个实例 / 226.106 µm²** |
| `hardware/soc/rtl/common_cells/cdc_2phase.sv` | +4/−4：拆分声明 + `ASYNC_REG` 属性 | 属性被 Genus 忽略，无综合后果 |
| `hardware/soc/rtl/clk_rst/rstgen_bypass.sv` | +1/−1：`ASYNC_REG` 属性 | 同上 |

`i_rstgen_tck` 在层级面积表里可单独看到，占 post-route 面积 0.006%。

## 3. 综合

| 指标 | 本次（128d454） | 旧基线（9d25b09） | 差异 |
|---|---:|---:|---:|
| 映射单元数 | 104,941 | 94,601 | +10.9% |
| Cell area（physical library） | 3,444,524 µm² (3.445 mm²) | 3,382,205 µm² (3.382 mm²) | +1.8% |
| SRAM memory area | 401,444 µm² | 401,444 µm² | 0 |
| 综合 setup WNS | **+0.002 ns**（10 条展示路径全 MET，0 VIOLATED） | +7.521 ns | −7.5 ns |
| 硬宏实例 | 1 | 1 | 0 |

综合映射差异的具体表现（按单元类型统计）：旧基线网表用 `SDFQD0BWP7T` 31,115 个作为
带使能触发器、**无** `CKLNQD`；本次网表用 `DFQD1BWP7T` 27,285 + `DFD1BWP7T` 2,784 +
`CKLNQD1BWP7T` 1,718（离散 ICG），另有 `DFKCND` 2,836。两者都在 20 ns 内收敛，但单元数与
cell area 不同。旧基线的 Genus 配置没有随流程目录保留，因此无法复现到逐位一致。

## 4. 布局布线几何

| 指标 | 本次 | 旧基线 |
|---|---:|---:|
| Die | 3100.16 × 3100.16 µm（9.611 mm²） | 同 |
| Core | 2800 × 2800 µm（7.84 mm²） | 同 |
| Core-to-die margin | 150.08 µm | 同 |
| post-route `report_area` | **3,647,279.552 µm²（3.647 mm²）** | 3,669,029.593 µm²（3.669 mm²） |
| `report_area` 实例数 | 107,585 | 99,662 |
| `report_area` / core 面积 | **46.52%** | 46.80% |
| Innovus placement density（旧基线口径） | 43.68%（优化中快照）/ 100% 含 filler | 42.983% |
| 含 filler 后密度 | 100.000% | 100.000% |
| SRAM 位置 | `i_mainmem/i_mainmem` @ (250, 650)，halo 26 µm | 同 |

> 密度有两个口径：`report_area`/core（本表第 3 行，可跨版本比较）与 Innovus 自己报的
> placement density（第 4 行，受 filler 和优化阶段影响）。两者不要混用。

## 5. 时序与物理检查

| 检查 | 本次 | 旧基线 |
|---|---:|---:|
| Setup WNS / TNS | **+0.886 ns / 0.000 ns** | +3.625 ns / 0.000 ns |
| Setup 违例路径 | 0 | 0 |
| Hold WNS / TNS | **+0.020 ns / 0.000 ns** | +0.023 ns / 0.000 ns |
| Hold 违例路径 | 0 | 0 |
| Innovus `verify_drc` | **0 violations** | 0 |
| `verifyProcessAntenna` | **0 violations**（修复后） | 0 |
| Core PG connectivity | Found no problems or warnings | 干净 |
| Regular connectivity | 2 条 info：`VDDPST/VSSPST` 无布线（与既有流程一致） | 同 |

### 天线修复记录

postRoute 检查有 10 处天线违例，流程自带的 `-fix_antenna` 路径未清除；hold 优化后剩 1 处：

```text
sram_wdata[1] (4)
  i_mainmem/i_mainmem (RA1SHD_2048x32M8) D[1]
  [1] METAL3: Area 8.6110 S.Area 32.3989 PAR 409.0770 Ratio 400.0000
```

处理方式：在该条网络上 `editDelete -net {sram_wdata[1]} -type Signal` 后
`selectNet` + `routeDesign -selected` 重布，复检 **0 violations**，DRC 仍为 0，
时序复测 setup +0.886 / hold +0.020 不变。修复后的数据库 `my_soc_top_antennaFixed.enc.dat`
是本次所有交付文件的来源。

## 6. 层级面积（post-route `report_area -depth 2`）

| 层次 | 实例数 | 面积 (µm²) | 面积 (mm²) | 占比 |
|---|---:|---:|---:|---:|
| `my_soc_top` | 107,585 | 3,647,279.6 | 3.647 | 100.00% |
| `i_npu_subsystem` | 75,450 | 2,203,458.3 | 2.203 | 60.41% |
| `i_cpu`（CV32E40P） | 14,476 | 349,802.9 | 0.350 | 9.59% |
| `i_axi_xbar` | 10,260 | 281,905.4 | 0.282 | 7.73% |
| `i_mainmem`（含 SRAM 宏） | 10 | 388,359.1 | 0.388 | 10.65% |
| `i_dm_top`（调试模块） | 2,619 | 60,003.6 | 0.060 | 1.65% |
| `i_bootram` | 991 | 32,943.4 | 0.033 | 0.90% |
| `i_axi2boot` | 399 | 9,755.5 | 0.010 | 0.27% |
| `i_axi2npu` | 493 | 12,249.2 | 0.012 | 0.34% |
| `i_axi2sram` | 455 | 11,320.6 | 0.011 | 0.31% |
| `i_npu_dma` | 369 | 11,922.1 | 0.012 | 0.33% |
| `i_dmi_jtag` | 743 | 21,868.6 | 0.022 | 0.60% |
| `i_dm_axi2mem` | 511 | 12,078.0 | 0.012 | 0.33% |
| `i_axi_adapter_*`（4 个） | 592 | 13,948.3 | 0.014 | 0.38% |
| `i_rstgen_main` | 10 | 239.3 | 0.0002 | 0.007% |
| **`i_rstgen_tck`（main 新增）** | **8** | **226.1** | **0.0002** | **0.006%** |

## 7. SDF 门级后仿

| 指标 | 本次 | 旧基线 |
|---|---|---|
| 网表 | `my_soc_top_hier_postpnr.v`（17.4 MB） | 同 |
| SDF | `my_soc_top_tt_io.sdf.gz`（MAXIMUM） | 同 |
| 激励 | `chipdesign_npu_test.hex`（main 仓库固件） | 同 |
| 结果 | **POSTLAYOUT_PASS: integrated SoC OK after 32,226 cycles** | PASS，32,226 cycles |
| 结束时间 | 644,670 ns | 同量级 |

周期数与旧基线逐字一致（32,226），这是"14 行 RTL 改动不影响功能"的最直接证据。

## 8. 与旧基线的差异说明

1. **功能等价**：后仿周期数完全相同；新增逻辑只有 TCK 域复位同步器（226 µm²）。
2. **面积等价**：post-route `report_area` 3.647 vs 3.669 mm²（−0.6%），SRAM 宏面积完全相同。
3. **综合口径不同**：本次综合单元数多 10.9%、cell area 多 1.8%、setup 余量少 2.7 ns。
   原因是旧基线网表使用 `SDFQD0` 做带使能触发器 + 无离散 ICG，而本次用 `DFQD1` +
   `CKLNQD1` 离散 ICG。旧基线的 Genus 配置未保留，属于**流程配置漂移，不是 RTL 差异**。
4. **验收线未退化**：DRC 0、天线 0、setup/hold 均正、后仿 PASS，与旧基线同级。

## 9. 证据位置

服务器（`/home/s2400012724/chip-fullstack-2026/ChipDesign_postsim_exps/pnr_main_align_std`）：

| 内容 | 路径 |
|---|---|
| 综合网表 / SDF | `syn/my_soc_top/my_soc_top_postsyn.v`、`my_soc_top_tt_1p80v_25c.sdf` |
| 天线修复 | `pnr/scripts/fix_antenna2.tcl`、`pnr/logs/antfix2.log` |
| 复导出 | `pnr/scripts/export_after_antfix.tcl`、`pnr/logs/export.log` |
| 最终审计 | `pnr/scripts/final_audit_aligned2.tcl`、`pnr/my_soc_top/reports/finalAudit/` |
| 时序 | `pnr/my_soc_top/reports/antfix_setup/`、`.../antfix_hold/` |
| 交付文件 | `pnr/my_soc_top/outputs/manual_final/`（DEF 185 MB、GDS 28 MB、SDF 20 MB、SPEF 51 MB、两种网表、SDC、LEF） |
| 后仿 | `build/postlayout_sim/xrun_postlayout_sdf.log` |

## 10. 局限

- 单角 TT/1.80 V/25 °C，未做 SS/FF、电压温度 MMMC、SI、IR/EM。
- Calibre DRC/LVS/PEX 不在本报告范围（见 `docs/ASIC_SIGNOFF_STATUS.md`）。
- 综合配置漂移导致绝对数字与旧基线不可逐项对比；如需严格对比，应在同一 Genus 配置下重跑两次。
- 天线修复只重布了 1 条网络，未重新做时序驱动优化；复测时序未变。
