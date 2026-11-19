# 第 3 周全栈芯片工程进展演示文稿（14 页，中英混排 ISSCC/VLSI 极简风）

沿用 [`../abi5`](../abi5) 的自底向上结构，精简至 **14 页**（去除独立分节目录页、合并全芯片签核矩阵与 ERC 审计、致谢并入尾页），采用中文叙述 + 英文专业术语，全面呈现第 3 周在 **架构与 RTL 验证 → 统一 ASIC 实现与物理签核 → 主机 JTAG 优化与 FPGA 延迟拆解 → 六场景应用与 Phase B 计划** 的实测进展。

## 字体与构建

100% TrueType (`.ttf` / `.ttc` `glyf`) 无衬线与等宽字体，确保 `pdf.js` 及各类 PDF 阅读器一致清晰渲染：
- 中文无衬线：**Microsoft YaHei**（`msyh.ttc` / `msyhbd.ttc`，fallback `PingFang SC` / `Noto Sans CJK SC`）
- 英文无衬线：**Segoe UI**（`segoeui.ttf` / `segoeuib.ttf`，fallback `Helvetica Neue` / `Liberation Sans`）
- 等宽代码：**Consolas**（`consola.ttf` / `consolab.ttf`，fallback `Menlo` / `Liberation Mono`）

```bash
python3 make_data.py          # 从仓库验证数据生成 data/ 宏与图表片段
latexmk -xelatex main.tex     # 编译 14 页 TrueType PDF（main.pdf）
python3 make_pptx.py          # 导出 14 页 16:9 PPTX 及演讲者备注（main.pptx）
./clean.sh                    # 清理 TeX 中间文件
```

## 页面结构（14 页）

1. **封面**：先进芯片全栈设计与实践 · 第 3 周进展
2. **总览**：端到端四大支柱核心指标与主线
3. **`npu_tensor_lookup` 迁移至 Activation SRAM**：设计动机、架构图、序关系不变量与 `-96.19%` 面积收益
4. **同基线 RTL 回归与 Mutation 覆盖**：7 模型冷运行周期变化、全量回归门控与 `7/7` 新增故障注入
5. **综合与布线后相对 FF-Lookup 基线的变化**：核心指标降幅柱图、总表与模块层级拆解
6. **物理版图、35-Pad Keepout 与功能闭合**：双 SRAM 对角布局、M1–M6 绕线、SDF/LEC/OpenSTA 闭合
7. **4-Corner MMMC STA、SRAM 接口与 452-Path CDC 闭合**：4 角 PVT 时序、`ss125` 瓶颈与典型 RC 说明
8. **窗口化 100%-Annotated VCD 功耗与算子融合收益**：5 负载 `busy → irq` 门级功耗与 `-55.98%` 融合节能
9. **物理收尾：Seal Ring、Dummy Fill 与 Keepout（`2000 DBU/µm`）**：原生封环、填充与 `3 µm` 保护区放大视图
10. **全芯片物理签核矩阵与晶体管级 ERC 审计**：统一 `postDummy.gds` 签核矩阵、`CO.R.4` 修复与 `7 Hier. / 977 Exp.` 逐管核账
11. **EGo1 实板延迟分解：三步排除与双瓶颈归因**：互斥驻留堆叠柱图、`1 → 5 MHz` 零收益与 `0.48–0.56 ms/word` 归因
12. **Host JTAG Burst 与 Single-RPC 流水化：分层实测证据与边界**：1 MHz RTL 线层下限、本地 `TCP+tclsh` 协议仿真与 `drscan` 剩余瓶颈
13. **六场景统一应用软件与验证**：`80/80` 固定输入、`112/112` zoo 参考帧、3 项健壮性修复与视觉基线
14. **诚实边界、Phase B 工程计划与致谢**：已验证基线 vs. 服务器真 MMMC & FPGA 实板批量后端计划

## 数据来源

`main.tex` 中全部指标数值与图表均由 `make_data.py` 从仓库实测档案动态生成到 `data/`（已 gitignore）：

- 优化前后综合/布线基线对比：`docs/validation/eda_latest_20261111/`
- 统一 Single-GDS ASIC 与完整物理签核：`docs/validation/eda_week3_lab5_20261119/`
- 7 模型同基线 RTL 周期对比与变异覆盖：`docs/validation/rtl_lookup_20261118/`、`hardware/npu/scripts/mutation_test.py`
- EGo1 实板频率扫描与分阶段延迟拆解：`verification/fpga/results/2026-11-11-jtag-frequency/`
- 1 MHz RTL 线层仿真与本地 `TclRpc` TCP+`tclsh` 仿真：`docs/validation/jtag_rpc_20261118/{rtl_1mhz_7models.json,tcl_tcp_sim.json,e2e_timing.json}`
- 六场景应用软件与视觉模型评测：`docs/validation/demo_software_20261116/summary.json`、`software/host/demos/models/{emotion,face}/training_result.json`、`software/host/demo.py`、`software/host/demos/signals.py`、`software/tests/test_demos.py`
